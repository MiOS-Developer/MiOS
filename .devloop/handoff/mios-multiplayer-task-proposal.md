# MiOS-Mons multiplayer: proposed canonical tasks

Proposed for root to register in `tasks.jsonl` and `TASKS.md`. Design: [mios-multiplayer-design.md](mios-multiplayer-design.md), revision 3, in the same directory. The baseline is worktree HEAD `fc4ad36ec`.

## Current canonical names

New work targets the `mios-mons` package/executable, `tools/native/mios-mons`,
`specs/mios-mons` and the layered `[mons]` SSOT table. The vendor default for
`[mons].state_directory` is `mios/mons` under `XDG_STATE_HOME`; preserve resolved
operator overrides rather than rewriting their paths. References below use the
current names, while source-line citations remain tied to the stated baseline.
The proposed modules, flags, schema fields and services still require their
listed implementation and acceptance checks. This naming update executes none
of those checks and changes no saved state.

**How to read this.** IDs `T00`-`T27` are local. Root assigns real task numbers. Each block maps onto the registry fields as follows:

| Block line | Registry field |
|---|---|
| Title | `title` |
| Phase | free text in `notes` |
| Priority | `priority` |
| Size | `size` |
| Workstream / domain | `workstream`, `domain` |
| Scope | `details.what_how` |
| Owned paths | `details.where` |
| Depends on | `depends_on` |
| Acceptance (positive) | `acceptance_criteria` and `verification.positive_cmd` |
| Acceptance (negative) | `verification.negative_control_cmd` |
| Root-owned | coordinator-only edits |

**Rules for every task:**
- Work in a worktree lane, with at most 4 lanes in total. Check `MemAvailable` against `[system.memory_admission]` before builds, benches and nested sessions. Stop processes by PID.
- Database tests run against a **disposable** pg18 instance started by the test, never the live `mios-pgvector` and never a live `mios-accountdb`.
- New code is Rust static binaries (Law 14), scaffolded with `mios new` (Law 16). New cargo dependencies must pass `tools/native/deny.toml` and be vendored for the offline bake (Law 12).
- Lanes propose SSOT fragments in their branch notes. Root applies every `mios.toml`, law-text, `[security.*]`, `[ports]`, `[firewall]`, sysusers, generator, `tasks.jsonl` and `mios-bootstrap` change, and does all pushes to `main`.

---

## Phase P0a: containment before account-plane enablement

### T00. SSOT, law and registry groundwork for accounts and multiplayer
- **Phase:** P0a (first) · **Priority:** P0 · **Size:** M · **Workstream / domain:** accounts / governance · **Lane:** root
- **Scope:**
  - Adopt the design as `docs/design/mios-mons-multiplayer.md` and register this task set under an epic.
  - Apply the Appendix A keys: new `[accounts]` keys, `[accounts.ranges]`, `[accounts.roles.*]`, `[accounts.federation]` and `[mons.*]`.
  - Allocate `[ports.categories.games]` with `mons_gateway` and `mons_ssh`, listed in `[ports].unbound` until T18 binds them.
  - Declare the sysusers and groups.
  - Record the operator's decisions on Q1 (naming), Q2 (OS password model) and Q3 (Law 11 amendment) in `[laws]` and in `CLAUDE.md`/`AGENTS.md`.
- **Owned paths:** `usr/share/mios/mios.toml`, `usr/lib/sysusers.d/` (new `mios-mons.conf`), `CLAUDE.md`, `AGENTS.md`, `docs/design/`, `tasks.jsonl`, `TASKS.md`.
- **Depends on:** none. Q1/Q2 are answered in revision3; Q3 remains unresolved.
- **Acceptance (positive):**
  - `mios_var_closure` passes: every new `MIOS_ACCOUNTS_*` and `MIOS_MONS_*` name a consumer references is emitted.
  - `check_ports_bound` and the SSOT lint pass.
  - The Python `mios_toml.py` and Rust `mios-resolver` return identical values for every new key, shown by a new parity test.
- **Acceptance (negative):**
  - A user-tier fragment setting `accounts.roles.player.groups = ["wheel"]` resolves to the vendor value for system consumers, in both resolvers.
  - No new key holds a password, hash, token or private key.
  - `[security.privileged_quadlets].root` still has exactly 12 entries.

### T01. Remove password material from the `account_sync` NOTIFY payloads (expand step)
- **Phase:** P0a · **Priority:** P0 · **Size:** S · **Workstream / domain:** accounts / security · **Lane:** identity
- **Scope:** Rewrite `notify_account_changes()` and `notify_alias_changes()` (`usr/share/mios/postgres/schema-init.sql:1292-1336`) to emit only `{table, op, id, version}`. Nothing consumes the channel today, so no consumer changes. Add a drift check, `check_notify_payload_no_secrets`.
- **Owned paths:** `usr/share/mios/postgres/schema-init.sql` (the two trigger functions); a new check under `automation/` (root registers it).
- **Depends on:** none.
- **Acceptance (positive):** On a disposable pg18 instance, `LISTEN account_sync` followed by `INSERT`, `UPDATE` or `DELETE` on `account` or `mios_identity.aliases` yields payloads whose keys are exactly `{table, op, id, version}`.
- **Acceptance (negative):**
  - No payload for any table or operation contains a key or value matching `hash|password|ntlm`.
  - A planted trigger that re-adds `password_hash` makes the new check fail.

### T02. Interim hardening of `mios-account-sync` until T21 retires it
- **Phase:** P0a · **Priority:** P0 · **Size:** M · **Workstream / domain:** accounts / security · **Lane:** identity
- **Scope:**
  - Delete the hash write-back path (`update_db_password` and its call sites, `mios-account-sync:38-59`, `:242-247`).
  - Send all SQL through `mios-pg-query --exec-json` with bound parameters, replacing the f-strings at `:173` and `:180`.
  - Validate every row:
    - the name against `[accounts].name_regex` and `reserved_names`;
    - uid inside an `[accounts.ranges]` tier;
    - gid equal to the per-user group or inside the tier;
    - groups inside the role allow-list. Elevated groups are never authorized by database is_admin. Operator/recovery identities need independently trusted root-owned policy and stay outside bulk player synchronization.
    - shell inside the allow-list. The `/bin/bash` fallback at `:114` is removed.
    - home equal to `/var/home/<name>`.
  - Put `--` before every positional argument.
  - Set passwords with `chpasswd -e` on stdin, never on argv.
  - Keep state as `{name: version}`, written 0600 from creation and renamed atomically.
  - Use absolute tool paths.
  - Parse output as JSON instead of tab-splitting.
- **Owned paths:** `usr/libexec/mios/mios-account-sync`, its tests. The unit block `[units."mios-account-sync.service"]` (sandbox keys) is root's.
- **Depends on:** T00 (grammar, ranges and allow-lists), T01.
- **Acceptance (positive):**
  - With a fake `mios-pg-query`, a valid row produces the expected `useradd`/`usermod` argv with `--`.
  - A changed hash version is applied through `chpasswd -e` on stdin.
- **Acceptance (negative):**
  - These rows are each refused, logged without any hash, and cause no command to run:
    - uid 0 or 999;
    - gid 0;
    - `groups = "wheel"` for a non-admin;
    - shell `/tmp/x`;
    - home `/etc`;
    - name `-o`, `../x` or `a'; DROP TABLE account;--`;
    - a display name containing a TAB.
  - A local `passwd` change never causes a database `UPDATE`.
  - Captured argv of every spawned command contains no `$y$`, `$6$` or `$2` string.
  - The state file is 0600 from the moment it exists.

### T03. Make file writes in `mios-userdb-render` and `mios-lldap-seed` safe
- **Phase:** P0a · **Priority:** P1 · **Size:** S · **Workstream / domain:** accounts / security · **Lane:** identity
- **Scope:**
  - Refuse names containing `/` or NUL, or failing the grammar.
  - Create privileged records and the bootstrap JSON with `O_CREAT|O_EXCL` at mode 0600 before writing, then rename atomically.
  - Redact hashes in dry-run output.
  - Delete records for disabled or removed accounts.
  - Rewrite the privileged record whenever the hash version changes, even when the public record did not change (the `continue` at `mios-userdb-render:99-100`).
- **Owned paths:** `usr/libexec/mios/mios-userdb-render`, `usr/libexec/mios/mios-lldap-seed`, their tests.
- **Depends on:** T00.
- **Acceptance (positive):** A rotated password updates the privileged record. A disabled account loses both its records.
- **Acceptance (negative):**
  - A name of `../../etc/x` writes nothing outside `/run/userdb`.
  - Dry-run output contains no `$y$` or `$6$`.
  - A privileged file is never observable with a mode other than 0600.

### T04. Contract step: drop NT-hash storage, the empty-hash default and the plaintext secret tables
- **Phase:** P0a · **Priority:** P0 · **Size:** S · **Workstream / domain:** accounts / security · **Lane:** identity
- **Scope:**
  - First inventory every reader. `git grep` also matched `automation/lib/globals.sh`, `automation/lib/globals.ps1` and `usr/share/mios/configurator/mios.html`.
  - Drop `mios_identity.aliases.password_hash_ntlm`.
  - Delete the empty-password NT default in `mios-ai-firstboot:395-400`. Nothing derives a Windows hash any more.
  - Convert `mios_security.headscale_preauth_keys.key_value` to `key_hmac`, or drop the table.
  - Drop `keepass_vaults.keyfile_blob`.
  - Root updates `[schema].unconsumed` and the redaction exemption.
- **Owned paths:** `usr/share/mios/postgres/schema-init.sql`, `usr/libexec/mios/mios-ai-firstboot` (the NT block), and any readers found.
- **Depends on:** T01.
- **Acceptance (positive):**
  - A fresh schema init succeeds, and so does an upgrade from the current schema on a disposable instance.
  - Firstboot completes on a host without MD4.
- **Acceptance (negative):**
  - `git grep -n 31D6CFE0` returns nothing.
  - `information_schema.columns` has no column matching `%ntlm%` or `keyfile_blob`.
  - A preauth key cannot be recovered from the database.

### T05. Network exposure: take pgvector off the firewall and add Mon mesh tags
- **Phase:** P0a · **Priority:** P0 · **Size:** S · **Workstream / domain:** network / security · **Lane:** root
- **Scope:**
  - Remove `pgvector` from `[firewall].open_ports` (`mios.toml:72`).
  - Add `tag:mon-client` and `tag:mon-gateway` with empty owners, and an ACL allowing `tag:mon-client → tag:mon-gateway:<mons ports>` only.
  - Put the existing `tag:guest`/`tag:workstation → tag:blade:*` rule (`headscale-policy.hujson:23-31`) to the operator, with a recommendation to narrow it.
- **Owned paths:** `usr/share/mios/mios.toml` `[firewall]`, `[metal.mesh]`, `[headscale]`; `usr/share/mios/mini/headscale-policy.hujson` and its generator.
- **Depends on:** T00.
- **Acceptance (positive):** The rendered firewalld service no longer opens the pgvector port. The policy parses, and the policy unit test passes.
- **Acceptance (negative):** In ACL evaluation tests:
  - `tag:mon-client → tag:blade:22` is denied;
  - `tag:mon-client → tag:mon-gateway:<any non-mons port>` is denied.

## Phase P0b: cut the default-credential chain

### T06. Remove shared service secrets; retain one protected user password across OS logons
- **Phase:** P0b · **Priority:** P0 · **Size:** L · **Workstream / domain:** credentials / security · **Lane:** root, bootstrap lane
- **Scope:**
  - **Vendor default.** Set `[identity].default_password` to `""` in the vendor tier. `mios_credentials.py` fails closed on an empty value, as it does today.
  - **Code fallbacks.** Remove every `or "<literal>"` credential fallback: `portal.py:81-84`, `mios-ttyd-launch:52-55`, `mios-userdb-render:22`, `mios-lldap-seed:22` and `seed-db-config.py:30`.
  - **Generation.** Bootstrap generates distinct machine/service secrets for portal transport, ttyd transport and databases into the approved 0600 credstore. A person's permitted Linux and Windows/MiOS-Xbox accounts use that account's one protected OS password, as D3 requires; do not generate conflicting per-surface human passwords. Protected recovery/administrative accounts remain independently scoped.
  - **`install.env`.** `system-sync-env.sh:79` stops writing credentials into `install.env`.
  - **ttyd.** Its credentials come off argv: ttyd moves behind the portal, or reads them from a file.
  - **Break-glass.** Document a break-glass account and test it.
- **Owned paths:** `usr/lib/mios/mios_credentials.py`, the scripts listed above, `usr/libexec/mios/system-sync-env.sh`, and the `mios-bootstrap` installer. The `[identity]`, `[portal]`, `[auth]`, `[autounattend]` and `[ttyd]` sections are root's.
- **Depends on:** T00, T02, T03. The operator answers Q10.
- **Acceptance (positive):**
  - In a nested MiOS-DEV VM install, service principals use distinct generated secrets while a person's one account password works for its permitted Linux and Windows/MiOS-Xbox logons; no ungranted surface or admin role is added.
  - With a 0-byte host fragment, the break-glass login still works.
- **Acceptance (negative):**
  - A new gate, `check_no_default_credentials`, finds no credential fallback literal in code.
  - `install.env` contains no `*PASSWORD*`, `*HASH*` or `*TOKEN*` key.
  - `/proc/<ttyd pid>/cmdline` contains no credential.
  - Two installs produce different generated machine/service secrets; this does not require changing a person's existing realm password.
  - A surface with no generated value refuses login and never accepts a default.

## Phase P1: measure, and fix the client CPU loop

### T07. CPU and memory measurement harness for MiOS-Mons
- **Phase:** P1 · **Priority:** P1 · **Size:** M · **Workstream / domain:** mons / performance · **Lane:** game
- **Scope:**
  - A render bench for `mios-mons` on `TestBackend` at 80×24, 120×40 and 200×60, for each renderer and for the voxel and pixel views. It prints JSON: ns per frame, and frames per minute while idle.
  - A cgroup reader (`cpu.stat`, `memory.current`) that the later `mios-monsd bench` reuses.
  - Commit the baseline numbers with the commit hash.
- **Owned paths:** `tools/native/mios-mons/benches/` or a `src/bin/mons-bench.rs`; `specs/mios-mons/mons-perf-baseline.md` (new).
- **Depends on:** none.
- **Acceptance (positive):** The bench runs headless in CI in under 60 s and emits valid JSON. The baseline is committed.
- **Acceptance (negative):**
  - The bench refuses to start below `[system.memory_admission].min_available_mb`.
  - A planted regression (one extra draw per frame) is flagged by the ratchet comparison.

### T08. Redraw only when something changes, with an idle frame policy
- **Phase:** P1 · **Priority:** P1 · **Size:** M · **Workstream / domain:** mons / performance · **Lane:** game
- **Scope:**
  - In `ui::run` (`ui.rs:5050-5190`), stop drawing on every iteration (`:5101-5104`).
  - Draw only on:
    - input;
    - a state change (tick, care, menu);
    - an animation frame for a moving or reacting entity, or an active camera or zoom transition;
    - a resize;
    - a native-dialog event.
  - Otherwise wait until the next deadline the loop actually needs.
  - Add an SSOT idle cap, `[mons].idle_fps`, which root applies.
  - Coordinate with pending task #3755, which also edits `ui.rs`.
- **Owned paths:** `tools/native/mios-mons/src/ui.rs` (run loop), `src/config.rs`.
- **Depends on:** T07.
- **Acceptance (positive):**
  - Budget B5 is met: at least 50 % less idle CPU than the T07 baseline.
  - All existing graphics and snapshot tests pass.
  - Frames at animation times are identical to the baseline.
- **Acceptance (negative):**
  - A scripted sleeping, non-reacting pet shows no more than `idle_fps` draws per second over 10 s, counted on the backend.
  - A resize still redraws within one poll.
  - A key press is drawn within one animation frame.

## Phase P2: split the core

### T09. Extract `mios-mons-core` and move pet motion into it, using integer math
- **Phase:** P2 · **Priority:** P1 · **Size:** L · **Workstream / domain:** mons / architecture · **Lane:** game
- **Scope:**
  - Create `tools/native/mios-mons-core` with `game`, `genetics`, `habitat`, `progression`, the character data, the save envelope and validation, and a new `motion` module.
  - `motion` uses fixed-point position and speed and a yaw lookup table. The server decides wander, using an injected RNG trait. It replaces `Ui.position`/`destination`/`yaw`/`visual_rng` (`ui.rs:144-148`) and the `f64::atan2` in `simulate_behavior` (`ui.rs:683-717`).
  - `mios-mons` depends on the core, and offline behaviour stays unchanged.
- **Owned paths:** `tools/native/mios-mons-core/**` (new), and in `tools/native/mios-mons/src/` the files `game.rs`, `genetics.rs`, `habitat.rs`, `progression.rs`, `save.rs`, `characters.rs` and `ui.rs`. The workspace `members` and the `[rust.categories]` entry are root's.
- **Depends on:** T08, which must land first to avoid `ui.rs` conflicts.
- **Acceptance (positive):**
  - Every existing MiOS-Mons test passes.
  - The same seed and intent stream produce an identical `Game` and `Motion` after 10,000 ticks, across three runs and in both debug and release builds.
  - Schema-4 saves load byte-identically.
- **Acceptance (negative):**
  - `cargo tree -p mios-mons-core` contains neither ratatui nor crossterm.
  - A grep gate finds no `Command::` in the core and no `f32` or `f64` in `motion`.

## Phase P3: protocol, headless room, local multiplayer

### T10. Protocol crate and its OpenAI-format JSON Schema
- **Phase:** P3 · **Priority:** P1 · **Size:** M · **Workstream / domain:** mons / protocol · **Lane:** game
- **Scope:**
  - Create `tools/native/mios-mons-proto` with:
    - `u32` big-endian length framing, capped at `[mons.room].max_message_bytes`;
    - `deny_unknown_fields` message enums (§6.5 of the design);
    - a `PetView` without `rng`;
    - text sanitizers: NFKC; strip `Cc`, `Cf`, bidi and zero-width characters; length caps; a confusable skeleton.
  - Generate `usr/share/mios/schemas/mons-protocol.json`, with a drift check.
- **Owned paths:** `tools/native/mios-mons-proto/**`; the generated schema file. Root registers the drift check.
- **Depends on:** T09.
- **Acceptance (positive):** Property tests round-trip every message. The schema regenerates byte-identically.
- **Acceptance (negative):**
  - An oversized frame is rejected before its body is read.
  - An unknown field is rejected.
  - Serialized `PetView` never contains `rng`.
  - A name containing U+202E or ESC renders inert.
  - A fuzz target runs 60 s without a panic.

### T11. Headless authoritative room, `mios-mons-room`
- **Phase:** P3 · **Priority:** P1 · **Size:** L · **Workstream / domain:** mons / server · **Lane:** game
- **Scope:**
  - A bin target in `tools/native/mios-monsd` that depends only on the core and the proto crate.
  - An `epoll` loop with a `timerfd` at `tick_seconds`; it can host N rooms, default 1.
  - Intent validation, and a room CSPRNG for duels.
  - Overlays never pause the room.
  - A checkpoint trait with a file-backed implementation for development.
  - After init it installs a `seccompiler` filter (no `execve`, no `AF_INET*`) and a Landlock rule set.
- **Owned paths:** `tools/native/mios-monsd/**` (new, room modules).
- **Depends on:** T10.
- **Acceptance (positive):**
  - Budget B1 is measured with T07's harness: at most one wakeup per tick while idle, and RSS ≤ 8 MiB.
  - Four scripted seats complete care and memory sessions with the expected messages.
  - Keyframe positions are identical for clients at 1, 7 and 60 fps.
- **Acceptance (negative):**
  - `execve` and `socket(AF_INET)` fail after init.
  - Oversized, malformed and over-rate intents are rejected and counted.
  - A seat cannot act on another owner's pet outside room policy.
  - The room sources contain no `Command::new` (grep gate).

### T12. Thin-client mode for `mios-mons`
- **Phase:** P3 · **Priority:** P1 · **Size:** L · **Workstream / domain:** mons / client · **Lane:** game
- **Scope:**
  - `--connect` over UDS in P3, and over TLS from T18.
  - All presentation stays per client. The client applies snapshot, delta and keyframe messages and sends intents for every Care and guess action.
  - Process-spawning code (`dialogs.rs:484`, `:573`, `:926`; `input.rs:308`, `:383`) goes behind a cargo feature, `desktop`, that is on by default. A `presenter` feature set excludes it.
  - `visible()` (`dialogs.rs:386-392`) also strips `Cf` and bidi.
- **Owned paths:** `tools/native/mios-mons/src/**` (client paths), its `Cargo.toml` features.
- **Depends on:** T10, T11.
- **Acceptance (positive):**
  - Two clients see identical stats and positions at keyframe ticks.
  - Offline single-player tests are unchanged.
- **Acceptance (negative):**
  - A recording room shows the client never sends state objects.
  - The `--no-default-features --features presenter` binary contains no tmux, sh or kill spawning (grep or symbol check).
  - A menu opened on client A does not pause ticks seen by client B.

### T13. Local multiplayer: seats on one host, and one controller per seat
- **Phase:** P3 · **Priority:** P2 · **Size:** M · **Workstream / domain:** mons / input · **Lane:** game, with the inputd lane
- **Scope:**
  - A local socket at `/run/mios/mons/gateway.sock`, served by a minimal local gateway until T18.
  - `SO_PEERCRED` seat identity: uid → local seat now, uid → account through the userdb `mios.account_uuid` field after T20.
  - N local seats in one client, each mapped to a pad that `mios-inputd` assigned.
  - Nested-session seats.
- **Owned paths:** `tools/native/mios-mons/src/input.rs` (seat mapping); a seat-assignment API in `tools/native/mios-inputd` if one is needed, coordinated with that lane.
- **Depends on:** T12. Q6 decides guest seats.
- **Acceptance (positive):**
  - Two pads control two seats independently in one terminal.
  - Two terminals running as two uids join one room.
- **Acceptance (negative):**
  - Seat 1's pad never moves seat 2 (route isolation test).
  - An unassigned pad produces no intents.
  - An unmapped uid gets guest-seat policy, with nothing persisted.

## Phase P4: identity

### T14. `mios-accountdb`: a hardened Postgres instance for identity and game state, with its schema
- **Phase:** P4 · **Priority:** P0 · **Size:** L · **Workstream / domain:** accounts / data · **Lane:** identity, with root
- **Scope:**
  - **Quadlet**, generated from `[containers.mios-accountdb]`:
    - reuses the `pgvector:pg18` image;
    - `Network=none`, no pod, and a bind-mounted socket directory (0750, group `mios-accountdb-clients`);
    - the superuser password from `Secret=…,type=mount` through `POSTGRES_PASSWORD_FILE`;
    - no `install.env`;
    - non-root `User=`/`Group=` and `Delegate=yes`.
  - **Authentication:** a generated `pg_hba.conf` with the single line `local all all scram-sha-256`, and `listen_addresses=''`.
  - **Migrations:** a migrator oneshot creates the databases `mios_identity` and `mios_mons`.
  - **Roles:** `identityd`, `mons_broker` and `ident_audit_ro`, all `NOSUPERUSER NOBYPASSRLS`. Role passwords are generated at install into the credstore.
  - **API:** `SECURITY DEFINER` functions with a pinned `search_path`.
  - **Constraints:** CHECKs fed from a generated `policy_snapshot`, and an append-only audit table.
  - **tmpfiles:** declare the data directories.
- **Owned paths:** `usr/share/mios/accountdb/**` (new schema and migrations), `usr/lib/tmpfiles.d/mios-mons.conf`. The `[containers.mios-accountdb]` and `[units]` entries and the generator inputs are root's.
- **Depends on:** T00.
- **Acceptance (positive):**
  - On a disposable instance: `identityd` can call `ident_*` and `mons_broker` can call `mons_*`. Migrations are idempotent.
  - The Quadlet drift check and the Law 6 validator pass with no privileged entry.
- **Acceptance (negative):**
  - No TCP listener exists. A connection from the host or from `mios-ai.pod` fails.
  - A uid outside `mios-accountdb-clients` cannot open the socket.
  - `identityd` cannot `SELECT` `mios_mons` tables or `UPDATE` `account` directly, and `mons_broker` cannot read `mios_identity`.
  - Inserting an account with uid 0, or named `root` or `-x`, fails its CHECK.
  - No column in either database is named or typed like `%hash%`, `%password%` or `%ntlm%` (gate).
  - `podman inspect` shows no secret in the environment.

### T15. `mios-identityd`: accounts, grants, invites, device keys, tickets, manifests
- **Phase:** P4 · **Priority:** P0 · **Size:** XL · **Workstream / domain:** accounts / security · **Lane:** identity
- **Scope:**
  - **Service:** a Rust static service on UDS only, checking peers.
  - **Accounts and grants:** CRUD through definer functions; grants checked against role ceilings, with approvals and FIDO2 step-up for `step_up_caps`.
  - **Invites and enrollment:** invites stored as HMACs; device-key challenge and response; WebAuthn and ed25519-sk verification.
  - **Tickets and revocation:** Ed25519 tickets carrying `aud`, `epoch` and `jti`, valid for at most `ticket_ttl_s`; an epoch feed pushed to gateways and the portal.
  - **SSOT reconcile:** `[[accounts.users]]` from the host tier only, marked `source=ssot`.
  - **Realm keys:** the realm key, plus signed rotation records.
  - **Credstore:** OS-password yescrypt hashes, sealed per node and per VM.
  - **Manifests:** signed, node-scoped manifests with `version` and `expires_at`.
  - **Audit:** every operation is recorded.
- **Owned paths:** `tools/native/mios-identityd/**`. The generated unit is root's.
- **Depends on:** T14.
- **Acceptance (positive):**
  - Invite → enroll → ticket works, and the ticket verifies with the realm public key.
  - A grant approved with step-up appears in the scoped node's manifest.
  - A host-fragment user appears with `source=ssot`.
- **Acceptance (negative):**
  - Reusing an invite past `max_uses` fails, and so does an expired invite.
  - A self-service `os.login.*` request stays pending until approved.
  - Approving a `step_up_caps` grant without FIDO2 is refused.
  - A user-tier `accounts.users` entry is ignored.
  - After a disable, a ticket carrying the old epoch is rejected.
  - A database dump contains no password hash.
  - Node A's manifest carries no sealed field for node B.
  - `player` + `wheel` is refused.
  - Registration rate limits trip.

### T16. Registration and grant UX in the MiOS-Mons apps
- **Phase:** P4 · **Priority:** P1 · **Size:** L · **Workstream / domain:** mons / UX · **Lane:** game (TUI), portal lane (Bun/TS), Windows lane (`mios-launch`)
- **Scope:**
  - **TUI:** join a realm by invite; enroll a device key (0600 under the resolved `[mons].state_directory` plus `keys`, default `XDG_STATE_HOME/mios/mons/keys`); log in; request grants; an admin approval and creation view with ed25519-sk step-up.
  - **Portal:** a registration page using WebAuthn.
  - **Windows:** `mios-launch` with keys stored under DPAPI.
- **Owned paths:** `tools/native/mios-mons/src/account*.rs` (new), the `src/mios-portal` registration route, `tools/native/mios-launch`.
- **Depends on:** T15. Q1 is answered: game-client registration; rename the unrelated monitor.
- **Acceptance (positive):**
  - Each game client registers the shared identity and standard OS logon eligibility on permitted nodes under root-owned policy, using one cross-OS password.
  - An admin creates an account with OS grants from the TUI, with step-up.
- **Acceptance (negative):**
  - The private key file is 0600 and never printed or logged.
  - A gateway certificate that does not chain to the CA pinned by the invite is refused.
  - A client cannot exceed root-owned standard-player scope or grant elevated groups, node management or another account rights.

## Phase P5: hosting

### T17. Room containers: image bake, bound image, user-scope Quadlet, supervisor
- **Phase:** P5 · **Priority:** P1 · **Size:** L · **Workstream / domain:** mons / platform · **Lane:** game, with root
- **Scope:**
  - **Image:** `automation/5x-bake-mons-room.sh` and `etc/mios/containers/mons-room/Dockerfile` build `FROM scratch` with the static `mios-mons-room` and `USER 65532`, plus a bound-images symlink.
  - **Unit:** a generated `usr/share/containers/systemd/users/mios-mons-room@.container` with `ConditionUser=mios-mons-rooms` and the hardening in §5.3 of the design. Its cgroup caps come from `[mons.room]`.
  - **Account:** linger and a subuid range for `mios-mons-rooms`.
  - **Supervisor:** `mios-monsd supervisor` runs as a user unit. It admits rooms on `MemAvailable` and `max_rooms`, mints capabilities and reaps idle rooms.
- **Owned paths:** the bake script, the Dockerfile, `tools/native/mios-monsd/src/supervisor*.rs`. The generator entry, the `no_group_delegate` entry, sysusers and linger are root's.
- **Depends on:** T11, T00.
- **Acceptance (positive):**
  - Budgets B1, B6 and B7 are measured.
  - 50 rooms start and are reaped cleanly within lane limits.
  - The image is at most 8 MiB.
  - The distroless conformance check passes: the image has no `/bin/sh`.
- **Acceptance (negative):** These security-required tests pass:
  - inside a room, connecting to `127.0.0.1:<any host port>` fails;
  - `/etc/mios/install.env` is unreadable (not mounted);
  - `execve` fails;
  - writing to `/` fails.

  In addition:
  - a start below the admission threshold is refused;
  - the unit never starts for other users;
  - `privileged_quadlets.root` is unchanged;
  - `podman events` shows no image pull.

### T18. Gateway and broker: TLS/WSS edge, routing, persistence, abuse limits
- **Phase:** P5 · **Priority:** P1 · **Size:** XL · **Workstream / domain:** mons / server · **Lane:** game, with identity
- **Scope:**
  - **`mios-monsd gateway`:**
    - TLS 1.3 with rustls, WSS, and the local UDS;
    - tickets verified offline, plus the epoch feed;
    - per-IP, per-account and per-room caps;
    - handshake deadlines, and no allocation before auth;
    - per-seat type and rate enforcement.
  - **`mios-monsd broker`:**
    - the `mons_broker` role, delivered by `LoadCredential=`;
    - versioned saves;
    - `mons_import_pet` using `validate()`, with `provenance=local_import`;
    - events and quotas.
  - **Ports and firewall:** bind `[ports].mons_gateway`. Root changes the firewall only when `[mons].remote_enabled = true`.
- **Owned paths:** `tools/native/mios-monsd/src/{gateway,broker}*.rs`. The generated units and the `[firewall]` and `[ports]` changes are root's.
- **Depends on:** T15, T17.
- **Acceptance (positive):**
  - A LAN or tailnet client plays in a room.
  - Budgets B2 and B3 are measured.
  - A pet survives a room restart and a host reboot.
- **Acceptance (negative):**
  - 1,000 half-open connections keep gateway RSS bounded.
  - Tickets with the wrong `aud`, past expiry or with an old epoch are rejected.
  - The gateway has no database credential: no `LoadCredential` for the database, and a clean environment.
  - A concurrent save with a stale version is rejected.
  - An import with coins above `MAX_COINS` is rejected.
  - Imported pets are excluded from leaderboards.
  - With `remote_enabled=false` there is no external listener.

## Phase P6: render seats

### T19. Render seats for the web and phone SSH; ttyd retired for players
- **Phase:** P6 · **Priority:** P2 · **Size:** L · **Workstream / domain:** mons / remote · **Lane:** game
- **Scope:**
  - **Presenter:** `mios-monsd presenter` gives each seat a PTY pair, its own cgroup, seccomp and Landlock, and runs the client built with the presenter features.
  - **SSH:** a game-only SSH listener in the gateway, using russh, maps public keys to accounts.
  - **Web:** a static web client, a baked xterm.js plus a Bun/TS shim, over WSS, with an Origin allow-list.
  - **Limits:** `[mons.render]` sets `idle_fps` and the seat limits.
- **Owned paths:** `tools/native/mios-monsd/src/{presenter,ssh}*.rs`, `usr/share/mios/mons-web/**`. The xterm.js bake step is root's.
- **Depends on:** T18, T08.
- **Acceptance (positive):**
  - A phone SSH client with a registered key reaches the game, and so does a browser.
  - Budget B4 is measured.
- **Acceptance (negative):**
  - Shell, exec, subsystem and port-forwarding requests are all refused.
  - No PAM session and no `user-<uid>.slice` is created for a player (`loginctl`).
  - A foreign Origin is refused.
  - A disabled account's live seat closes within `revocation_sla_s`.
  - The per-seat memory cap holds against a huge terminal size.

## Phase P7: node agent and cutover

### T20. `mios-account-agent` (Linux): signed manifests to userdb, keys, groups, polkit, memory floors
- **Phase:** P7 · **Priority:** P0 · **Size:** XL · **Workstream / domain:** accounts / security · **Lane:** identity, with root
- **Scope:**
  - **Binary:** Rust static, split into `fetch` and `apply`. `fetch` is unprivileged and can reach the network. `apply` is privileged, has no network, runs under the §3.8 unit hardening and is triggered by a `.path` unit.
  - **Checks:** signature, node id, version and expiry; opens sealed fields; re-validates every field against the SSOT.
  - **Outputs:**
    - userdb records, 0600 and atomic;
    - an `AuthorizedKeysCommand` helper and an sshd `AllowGroups` drop-in;
    - groups from allow-lists;
    - polkit fragments for libvirt;
    - per-role `user-<uid>.slice` floors.
  - **Revocation:** `locked`, key removal, `loginctl terminate-user`.
  - **Stale manifests:** follows the Q4 policy.
  - **Break-glass:** the break-glass account is protected.
  - **Receipts:** signed apply receipts.
  - **Memory floor:** root narrows the vendor blanket floor at `mios.toml:834-836`.
- **Owned paths:** `tools/native/mios-account-agent/**`. The generated units, the sshd drop-in and the memory-floor drop-in change are root's.
- **Depends on:** T15. Q3 and Q4 must be answered.
- **Acceptance (positive):**
  - On a nested test VM, an `os.login.linux` grant becomes a working login within the SLA.
  - Disabling the account makes password login, SSH-key login and RDP fail within `revocation_sla_s`.
  - Per-role floors are visible through `systemctl show`.
- **Acceptance (negative):** These security-required tests pass:
  - each of these manifests is refused and nothing is written: uid 0, gid 0, `groups=wheel` for a player, a shell outside the allow-list, a home outside `/var/home`, unsigned, the wrong node id, a lower version, expired;
  - the break-glass account is untouched, even by a manifest that names it.

  In addition:
  - `apply` opens no `AF_INET` socket and holds no database credential;
  - no hash appears in the journal or the state;
  - a node-local `passwd` change is reverted and alerted, never propagated.

### T21. Cutover: migrate the pgvector account tables and retire the Python account daemons
- **Phase:** P7 · **Priority:** P1 · **Size:** L · **Workstream / domain:** accounts / migration · **Lane:** identity, with root
- **Scope:**
  - One-time import of names, displays and roles from `account` and `mios_identity` into `mios-accountdb`, validated against the grammar.
  - Hashes are **not** migrated. Users set new OS passwords, or the SSOT bootstraps them through credential references.
  - Bootstrap the operator from the SSOT.
  - Remove `mios-account-sync`, `mios-userdb-render` and `mios-lldap-seed`. LLDAP, if kept, is fed from manifests.
  - Expand and contract: deprecate the old tables in `[schema]`, then drop them.
- **Owned paths:** the three retired scripts, `schema-init.sql` (deprecations and drops), and a migration subcommand in `mios-identityd`. The `[units]` removals are root's.
- **Depends on:** T20, T06.
- **Acceptance (positive):** After cutover the operator and break-glass logins work, and every account has the expected grants.
- **Acceptance (negative):**
  - A gate finds no unit referencing a retired binary.
  - The pgvector schema has no password or hash column.
  - The `account_sync` triggers are gone.

## Phase P8: surfaces

### T22. Remote and VM projections: RDP, per-user portal auth, Guacamole, libvirt, Headscale
- **Phase:** P8 · **Priority:** P1 · **Size:** L · **Workstream / domain:** accounts / remote · **Lane:** identity, portal lane, root
- **Scope:**
  - **RDP:** project `mios-rdp` group membership.
  - **Portal:** per-user tokens from identityd replace the single-password HMAC; an Origin check on the terminal WebSocket; login rate limits and backoff; `Secure` cookies.
  - **Guacamole:** user projection.
  - **libvirt:** per-domain polkit rules.
  - **Headscale:** an adapter for users, HMAC preauth keys and tags per grant, plus TLS for the control plane.
- **Owned paths:** the portal code (grandfathered Python, or the Bun/TS portal), the Headscale adapter in `mios-identityd`, the polkit fragment generator. The `[headscale]` changes are root's.
- **Depends on:** T20.
- **Acceptance (positive):** Each grant type enables exactly its own surface.
- **Acceptance (negative):**
  - `vm.console:<A>` can neither start nor stop A, and cannot touch B.
  - Without `remote.rdp`, `mios-rdp-priv` denies the user.
  - A disabled account's portal token is rejected immediately.
  - A foreign Origin is refused.
  - 20 failed logins trigger backoff.
  - A preauth key cannot be recovered from the database.

### T23. Blade and node management grants, with FIDO2 step-up
- **Phase:** P8 · **Priority:** P1 · **Size:** L · **Workstream / domain:** accounts / metal · **Lane:** identity, metal lane, root
- **Scope:**
  - **Delivery:** project `node.login` and `node.manage:<blade>` only through that Blade's agent.
  - **Effect:** a userdb record, `wheel` on that Blade only, Cockpit access, a rendered k3s `ClusterRoleBinding` and a Ceph dashboard role.
  - **Bootstrap:** `fleet.admin` comes only from the host SSOT.
  - **Step-up:** FIDO2 on every management grant.
- **Owned paths:** the Blade profile of `mios-account-agent`, the k3s RBAC renderer, the Cockpit projection.
- **Depends on:** T20. Multi-Blade parts depend on tasks 929-935.
- **Acceptance (positive):** `node.manage:blade-a` makes the holder an administrator on blade-a.
- **Acceptance (negative):**
  - A guest-kind agent refuses every `node.*` record.
  - The grant gives nothing on blade-b.
  - A grant without step-up is refused.
  - No app can create `fleet.admin`.

## Phase P9: Windows

### T24. MiOS-Xbox account agent (Windows, Rust)
- **Phase:** P9 · **Priority:** P2 · **Size:** L · **Workstream / domain:** accounts / windows · **Lane:** Windows lane, with identity
- **Scope:**
  - **Binary:** the Windows target of `mios-account-agent`.
  - **Channel:** a virtio-serial manifest channel declared in the VM definition.
  - **Namespace:** manages only accounts with the name prefix and description tag.
  - **Passwords:** sealed delivery of the OS password, then an acknowledgement.
  - **Administrators:** touched only through `os.admin.windows`.
  - **Signing and logs:** code-signed under `[security.powershell_signing]`; logs to the serial port.
  - **No application-owned NT hash** in databases, manifests, logs or credential bridges. Native Windows credential storage is an OS boundary, not a portable application hash shortcut.
- **Owned paths:** `tools/native/mios-account-agent` (Windows `cfg`), and the channel and install step in `usr/libexec/mios/win/xbox/`. The `[windows.xbox.*]` changes are root's.
- **Depends on:** T20, T15. Q2 is answered: one password for permitted Linux/Windows/MiOS-Xbox logons.
- **Acceptance (positive):**
  - An `os.login.windows` grant lets the user log on with their OS password.
  - Revocation disables the account within the SLA while the VM is running.
- **Acceptance (negative):**
  - A planted local admin outside the namespace is untouched.
  - An unsigned manifest is refused.
  - No NT hash appears in the database, a manifest or a log.
  - No new network listener or WinRM use.
  - The sealed blob is deleted after acknowledgement or TTL.

## Phase P10: fleet

### T25. Fleet placement: rooms on k3s, identity HA and backups, manifests across Blades, Blade-published ingress
- **Phase:** P10 · **Priority:** P2 · **Size:** XL · **Workstream / domain:** mons / fleet · **Lane:** root, metal lane
- **Scope:**
  - Generate k8s Pod specs from `[mons.room]` (§5.7 of the design), plus a gateway DaemonSet.
  - Place the identity primary, with a failover runbook.
  - Back up with PITR to Ceph.
  - Distribute manifests to every Blade.
  - Publish ingress from the Blade's router core.
- **Owned paths:** the generators, `usr/share/mios/k3s/**` (new manifests), the runbook docs.
- **Depends on:** T18, T20, task_931, task_933, task_934, task_935.
- **Acceptance (positive):**
  - A room is scheduled on any node.
  - Losing the identity primary leaves logins working from cached manifests.
  - A restore from backup succeeds.
- **Acceptance (negative):**
  - Room Pods have no `hostNetwork`, no service-account token and a deny-all NetworkPolicy.
  - While the `k3s-multi-server` hazard is still accepted, the fleet path refuses to enable (gate).
  - No guest becomes a mesh peer.

## Phase P11: federation

### T26. Realms, federation and hosted rooms
- **Phase:** P11 · **Priority:** P2 · **Size:** L · **Workstream / domain:** accounts / federation · **Lane:** identity
- **Scope:**
  - **Realm init:** a per-realm SID prefix replacing the fixed one at `mios-ai-firstboot:435`, realm uid ranges and a realm key.
  - **Trust:** `[[accounts.federation.trusted]]` pins each foreign realm's key, with a cap ceiling.
  - **Principals:** foreign users are `user@realm` and get no uid.
  - **Revocation:** signed revocation lists.
  - **Hosting:** room quotas per foreign realm.
  - **Prerequisite:** root flips `[security].api_require_auth` and `principal_bind_mode` to enforce.
- **Owned paths:** `tools/native/mios-identityd` (federation modules), the realm-init step in `mios-bootstrap`. The `[security]` changes are root's.
- **Depends on:** T18, T22, the enforce flip. Q7 remains unresolved; Q8 is answered: LAN/mesh only in v1. Public hosting needs future authorization.
- **Acceptance (positive):** A user from realm A plays in a room in realm B with A's ticket.
- **Acceptance (negative):**
  - **Security-required test:** a federated principal can never obtain `os.login`, `remote.*`, `vm.*` or `node.*`.
  - A ticket from an untrusted realm is rejected.
  - A peer found through mDNS or a2a gains no trust.
  - A revoked foreign user is rejected after the next list refresh.
  - Two realms get different SIDs and uids.

---

## Critical path and parallelism
- **Containment and identity:** T00 → T01 → T02/T03/T04/T05 → T06 (P0, the identity lane and root). This runs in parallel with the game.
- **Game:** T07 → T08 → T09 → T10 → T11 → T12 → T13 (the game lane).
- **Identity build-out:** T14 → T15 → T16. It can start after T00 and runs alongside the game lane.
- **Hosting:** T17 needs T11. T18 needs T15 and T17. T19 needs T18.
- **Projections:** T20 needs T15. T21 needs T20 and T06. T22, T23 and T24 need T20.
- **Fleet and federation:** T25 and T26 are last, behind external tasks 931/933/934/935 and the security enforce flip.

Suggested lanes, at most 4 at once: root, identity, game, and portal or Windows rotating in as needed.

## Coverage of the security critique's required negative tests

| Required negative test | Task |
|---|---|
| A local unprivileged uid cannot connect to the identity DB | T14 |
| A row with uid 0, gid 0 or `groups=wheel` is refused by the agent | T02 (interim), T20 |
| A disabled account loses SSH-key login, sessions and room tickets on every node | T19, T20, T22 |
| A federated `user@origin` cannot obtain `os.login` | T26 |
| A room cannot reach `127.0.0.1:<port>`, cannot `execve` and cannot read `install.env` | T17 |
| A peer name containing U+202E or ESC renders inert | T10, T12 |

## Revision3 additional v1 gate

### T27. Social rooms, trading and leaderboards alongside visits, co-care and duels
- Phase: v1 delivery gate after authoritative rooms, identity and networking; not a post-v1 deferral.
- Scope: variable-size seeded/furnished social rooms, visits, permissioned co-care, memory duels, atomic owner-approved trading, server-provenance leaderboards, safe chat/thoughts and focused dialogs with inline fallbacks. Native/remote/mobile/watch clients share authoritative simulation. Controls navigate menus/HUDs/cameras; RNG simulation owns Mon locomotion.
- Owned paths: proposed room/core/protocol/client modules; root assigns them after architecture review.
- Depends on: T11/T12/T16/T17/T18/T19 and containment. V1 endpoints are LAN/mesh only.
- Positive acceptance: multiple real clients use different room sizes, co-care with consent, finish duels, exchange items/currency without duplication, see authoritative scores, reconnect to server state and navigate tiny/portrait/landscape prompts.
- Negative acceptance: reject unauthorized care/trades, stale/replayed intents, double-spends, forged scores, imported-pet leaderboard eligibility, oversized rooms/chat and cross-session controller leakage without altering legitimate state. No WAN/public listener or default elevated group.
- Root-owned: canonical IDs, SSOT, exposure, credential-store approval and deployment.

Q1/Q2/Q5/Q8 are answered in MULTIPLAYER.md. Native Windows verifiers differ from application-owned hash storage. Admission covers real OS sessions. Monitor renaming covers consumers/generated launchers. Other questions and the separate-database proposal remain unapproved. No live identity/database/network change is authorized.


## Future Mon-World proposals (research addendum, 2026-11-07)

Source: MON-WORLD-RESEARCH.md; constraints: MULTIPLAYER.md §1.4.
W01-W05 are local IDs only, deferred until after current R3/camera/menu work.
Existing containment, identity, room-container and LAN/mesh v1 gates remain.

- W01: versioned deterministic room-lot coordinates and scene data. Verify identical generation across arrival orders/processes, shared entity IDs and compact-code roundtrips. Reject malformed coordinates; view/frame changes cannot alter RNG; seed codes cannot authorize access.
- W02: bounded isometric voxel neighbourhood and continuous rotation. Measure a 3x3 room working set, culling and coarse voxel LOD. Verify boundary/occlusion/furnishing/Kaomoji behavior across terminal sizes. Long traversal must not grow residency without bound; late generation cannot overwrite edits; four-view caches cannot impersonate continuous geometry.
- W03: horizontal depth-lane view of the same world. Switch repeatedly between views with identical authoritative entities, needs, inventory and motion. No duplicate pets/rooms, clock advancement or physics/RNG changes; tiny/edge-transition views remain navigable.
- W04: social Navigator/minimap and focused join/doorbell prompts. Use existing visits/co-care/duels/trading/rights rather than inventing a second identity plane. Test several clients/view sizes and inline fallback. Refuse stale approval/replayed joins; seed codes/client labels confer no rights; registration grants no elevated OS groups.
- W05: measured many-instance hosting and rendering. Compare per-room containers with an explicitly experimental shared-city process under equivalent isolation/workload. Publish source/artifact/node context, RSS, idle wakeups, occupied CPU, cold start and frame costs at several concurrency levels. Admission must fail safely without lowering thresholds or closing other sessions. Preserve D6 unless a hosting alternative is explicitly reviewed; no GPU/pixel-count extrapolation counts as a passed CPU gate.

No identity, database, account, service, firewall or public listener change is authorized by this addendum.
