# MiOS-Mons multiplayer and the global account backbone

Revision 3, 2026-11-06; research addendum 2026-11-07. Design proposal; not deployed. Baseline: worktree HEAD `fc4ad36ec`
(`/.worktrees/mios-inputd-camera-sticks`). All `file:line` citations are at that commit
unless they say otherwise. Revision 1 was never saved. This revision was rebuilt from its
outline and answers both critiques:
- `critique-security.md`: on disk, 31.7 KB.
- `critique-laws-ops.md`: never written. Only a truncated inline copy reached the
  architect. Appendix C says how the missing part was covered.

Nothing in this document was run. Every CPU and memory figure is an estimate. §7.4
defines the harness that turns each one into a measured number before any budget becomes
a gate.

---

Q1/Q2/Q5/Q8 are answered: game clients own registration and the unrelated monitor is renamed; one password works for permitted Linux and Windows/MiOS-Xbox logons; v1 includes visits/co-care/duels/trading/leaderboards and hotel-style social rooms of varying sizes; exposure is LAN and mesh only. Other architecture choices, including a separate database service and any Law11 amendment, remain unapproved proposals. No live identity/database/service/network change is authorized. Revision2 is preserved in mios-design-prior/.

## 0. Decisions at a glance

| # | Decision |
|---|---|
| D1 | **One global account per person, identified by a UUID.** Every surface (game, OS logon, remote, VM, client device, Blade/Node management) is a separate **grant** on that account, never a flag. Roles are SSOT-declared bundles of grants. |
| D2 | **Proposed, not yet approved: a separate mios-accountdb instance**, reusing the bound pg18 image and existing MiOS account vocabulary. Keep identity/game schemas isolated, socket-only and authenticated with non-superuser service roles. Reconcile the separate-instance proposal with the existing-MiOS-database requirement before implementation. An agent-plane superuser connection cannot confer account authority. |
| D3 | **One user-chosen OS password per account works for permitted Linux and Windows/MiOS-Xbox logons.** Protect OS-specific verifiers with approved native stores and node-scoped sealed delivery. No plaintext, reusable NT hash or password-equivalent enters application databases, logs, argv or notifications. Native Windows credential storage is distinct from application-owned hash shortcuts (3.10). Device keys/passkeys may authenticate the same identity without creating another OS password. |
| D4 | **Nodes act only on signed, node-scoped entitlement manifests, never on raw database rows.** A new Rust `mios-account-agent` is split in two: `fetch` is unprivileged and has network access; `apply` is privileged, has no network, and re-validates every field against SSOT ranges and allow-lists. It replaces `mios-account-sync`, `mios-userdb-render` and `mios-lldap-seed`. Nodes are read-only replicas: no node ever writes credentials back. |
| D5 | **MiOS-Mons game clients own registration.** Create the shared game/OS identity with one cross-OS password under root-owned node enrollment policy. Logons are scoped to permitted nodes with standard-user group allow-lists. Registration grants no wheel/docker/libvirt/kvm/Administrators, Blade management or elevated rights. Higher grants require independent administrative approval and step-up. Rename the unrelated infrastructure monitor with its consumers. |
| D6 | **Rooms are minimal containers.** The image is `FROM scratch` plus one static musl binary (`mios-mons-room`). The container is rootless under a dedicated `mios-mons-rooms` account and runs `Network=none`, `ReadOnly`, `DropCapability=ALL`, `NoNewPrivileges`, with a per-instance MCS label and cgroup caps. Its only I/O is one UNIX socket. It holds no credentials, reads no `install.env`, and cannot spawn processes. |
| D7 | **The network edge is one gateway, `mios-monsd gateway`.** It terminates TLS 1.3 (native and WSS) and SSH (game-only, implemented in the gateway, never `sshd`). It verifies Ed25519 room tickets offline and multiplexes clients to rooms over UNIX sockets. Rooms get **no ports**, so per-room port ranges never exhaust. Persistence goes through a separate broker, `mios-monsd broker`, which is the only holder of the `mons_broker` database role. |
| D8 | **The room is authoritative and headless; clients are thin and render locally.** Host CPU then scales with rooms × tick rate, which is cheap, instead of viewers × voxel frame rate, which is what costs today. A viewer that cannot render (a phone over SSH, a browser) gets a sandboxed *render seat*. Render seats are damage-driven and capped by SSOT frame policy. |
| D9 | **The game is split into crates.** `mios-mons-core` holds the deterministic, integer-only rules plus pet motion, which moves out of `Ui`. `mios-tamagotchi` becomes the client and the single-player game. `mios-monsd` provides the room, gateway, broker and supervisor. Single-player play stays offline-capable, with local saves and no account. |
| D10 | **Players may have standard OS accounts on permitted Linux and Windows/MiOS-Xbox nodes.** This supersedes revision2 exclusion of OS users. Game-only SSH remains a gateway transport, not an OS login session. Explicit node scope, protected recovery accounts, group allow-lists and measured per-role memory admission are prerequisites; do not multiply the blanket10GiB floor by player count. |
| D11 | **Revocation is epoch-based.** Tickets carry `{sub, epoch, aud, exp ≤ 15 min}`. Gateways get a pushed epoch feed. Node manifests carry `expires_at`. Target SLA: ≤ 60 s on every online node. Disabling an account locks it, removes its keys, ends its sessions and evicts it from every room. |
| D12 | **A realm is one fleet, or one standalone host.** The identity primary is a guest-plane singleton per realm. Agents keep working from cached manifests while it is down. Federation uses only pinned Ed25519 realm keys from the host SSOT. A federated principal is `user@realm`, gets no local uid and is capped at `mons.*`. Discovery never creates trust. |
| D13 | **Blade-owned ingress exposes v1 on LAN and mesh only.** Gateway/rooms/identity remain guest-plane. No public/WAN listener, firewall publication or public hosted realm is enabled in v1. Blade management remains separately authorized and confined to Blade agents. |
| D14 | **Containment precedes account enablement.** Current account code accepts database-controlled OS parameters. Close that boundary and preserve break-glass access first. Verify runtime exposure/guards per node; the disabled local service is not evidence of a live fleet compromise. |
| D15 | Future Mon-World: seeded room-lot grids, rotatable isometric voxels and an optional horizontal depth-lane view share authoritative RNG pet simulation. Bounded3x3 room working-set proposal; menus/HUDs/cameras never steer Mons. Room-container isolation remains the baseline; cost/LOD alternatives need measurement. |

---

## 1. Goals and non-goals

### 1.1 Operator direction (verbatim)
1. "the Game must be able to be hosted, with; local and remote multiplayer--single hosts should be able to host MANY instances without extremely high CPU utilization"
2. "multiplayer implementation(s) should utilize MiOS database systems; with User accounts being how user(s) are added globally(with VM access, remote access, client access, Blade/Node access+management, OS User accounts(Windows(MiOS-Xbox) AND Linux!!!) that is the backbone of the MiOS-Mons multiplayer engine--with multiplayer rooms being hosted as minimal containers for clients to connect to--with user crendentials being registered through MiOS-Mon App(s), MiOS SSOT(for hosted, self-hosted, federated mesh-nodes/blades, etc-etc!!)"

### 1.2 Goals
- **G1. Hosting.** Any MiOS host can host MiOS-Mons rooms, with no cloud dependency.
- **G2. Local multiplayer.** Several seats on one host or one terminal, each with its own controller.
- **G3. Remote multiplayer.** LAN and mesh only in v1, using native/web/phone-SSH clients. Public/WAN hosting is outside v1.
- **G4. Density.** Many rooms per host at near-zero idle CPU (§7 budgets).
- **G5. One account backbone.** One account per person carries grants for game, OS (Linux and Windows/MiOS-Xbox), remote, VM, client device and Blade/Node management, projected to every node.
- **G6. Data on MiOS databases.** Accounts and game state live in MiOS Postgres (D2).
- **G7. Two registration paths.** Credentials and accounts are registered through the MiOS-Mons apps and through the SSOT. This works for hosted, self-hosted and federated realms.
- **G8. Law compliance.** All 16 laws hold, plus the Blade invariant and the rule "credentials come from the layered SSOT, with no hardcoded defaults".

### 1.3 Non-goals
- No MMO-scale sharding across realms in v1. One room lives on one host.
- No real-time action gameplay. The game simulates at `tick_seconds` (`mios.toml:20154`, default 2 s), and animation is client-side.
- No replacement for the agent-plane database. `mios-pgvector` keeps its role. Its own `trust`/RLS issues are listed in Appendix B and owned elsewhere.
- No live Postgres in any OS login path. Logins resolve from local projections (`docs/design/doc-postgresos-accounts.md` §3(a)).

---


### 1.4 Future Mon-World engine: research adoption

This addendum incorporates the operator's Mon-World direction and
[MON-WORLD-RESEARCH.md](MON-WORLD-RESEARCH.md), read on 2026-11-07.
It is future engine work, not a claim of implemented multiplayer or city rendering.
Current R3, camera, menu/HUD and account-containment gates remain separate.

**World and views.** The canonical Mon room is the lot unit; its dimensions come
from room data/SSOT, not copied emulator dimensions. Distinguish the streamed
world grid of room lots from each room's floor/voxel coordinates. A3x3 room
neighbourhood is a proposed detailed working set, not a 3x3 floor or unbounded
memory allocation. Mons, room surfaces and furnishings retain voxel scene data.
The default isometric camera follows the autonomous Mon and remains rotatable,
with Room/Pet/Face views. An optional horizontal view can project the same room
neighbourhood into front/back depth lanes with horizontal follow scrolling.

**Simulation and controls.** Both views read the same authoritative room/entity
state. Changing cameras or frame rate never advances RNG, moves a pet or edits
needs, stats or inventory. The core owns seeded traits, stages, training and
autonomous motion. Controls select menus/HUDs/cameras, with focused native dialogs
and inline fallbacks. Click-to-move or direct position/velocity steering is not
part of this pet contract; choosing a room through a menu is a social intent,
not control of the Mon locomotion axes. Desktop, portrait/landscape terminal, VT, SSH, mobile,
webview and watch clients keep readable faces and usable prompts. Preserve the
pause/offline-freeze contract; room occupancy scheduling does not authorize
offline need decay. Permissioned co-care activation must be specified explicitly.
Visits/co-care/duels/trading/leaderboards/social rooms remain T27's existing v1 gate.

**Deterministic streaming.** Use versioned seed/coordinate generation independent
of arrival order and client frame rate, a bounded resident working set and local
camera-relative projection. Compact birth/seed codes describe pets; they do not
authenticate accounts or confer room rights. Test chunk edits against late
generation and preserve phenotype generation across documented versions.

**Hosting baseline is unchanged.** D6's operator-requested minimal rootless room
containers, static binaries, unprivileged boundaries and LAN/mesh-only v1 still
apply. A shared scheduler is an implementation candidate; a container per city
instead of per room is a comparison experiment, not an adopted replacement.
Measure admission, RSS, idle wakeups, occupied CPU, cold start and many-client
rendering before setting budgets. This addendum enables no service, identity
projection, database or network listener.

**Research qualification.** Root spot-checked Kepler's
[room scheduler](https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/game/room/managers/RoomTaskManager.java)
and [doorbell handler](https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/messages/incoming/rooms/TRYFLAT.java).
They support occupancy-oriented scheduling and checking for a present rights
holder. They are emulator implementations, not MiOS defaults. The report's §12
doorbell comparison mixes absence and presence of a rights holder rather than
establishing two competing policies. Arcturus was not rechecked here. Pin exact
source revisions before borrowing implementation details.

Roxlap's versioned
[0.32.0 Grid documentation](https://docs.rs/roxlap-scene/0.32.0/roxlap_scene/struct.Grid.html)
provides a sparse-chunk/LOD/cache example. The report's older 0.4.2 billboard URL
was inaccessible in this review. This supports studying the pattern, not adopting
that crate or promising its frame rate. GPU-assisted benchmarks, calculated cache
sizes and pixel-count ratios are not measured MiOS terminal budgets. Refuted
pathfinding/storage/sorting claims remain excluded.

**Rendering experiments.** Test visibility culling, bounded chunk generation,
coarse voxel LOD and optional derived-view caches. A four-snapshot cache applies
only to an explicitly discrete four-view mode; it cannot silently replace a
continuously rotatable voxel scene. Validate occlusion, animation, tracked readable
Kaomoji faces and rotation continuity on actual target terminals. The horizontal
depth-lane view and detailed rendering of nine rooms remain proposals requiring prototypes.

W01-W05 below are local future proposals, not canonical claims, active lanes or
deployment approval. The supplied research report remains unchanged.

Research input SHA-256: f97237d824b53ea242243393c713047d647c4c3d804d91aa9a99c85695b1d133

## 2. Trust diagram (security gate 1)

```
 player device                      guest plane (hosted node or standalone host)                    every node
 ─────────────                      ────────────────────────────────────────────                    ──────────
 native client ──TLS1.3 (pinned realm CA) + ticket──┐
 browser ───────WSS (same cert) + ticket────────────┤
 phone SSH ─────SSH pubkey (gateway-terminated)─────┼─> mios-monsd gateway ──UDS + per-room cap──> mios-mons-room@<id>
 local seat ────UDS + SO_PEERCRED + ticket──────────┘     │  (no DB creds, verifies              (Network=none, no creds,
                                                          │   tickets with realm pubkey)          no exec, one UDS)
                                                          │                                           │ UDS (room cap)
                                                          │ UDS (auth relay, no secrets stored)       v
                                                          v                                     mios-monsd broker
                                                   mios-identityd ──UDS scram (identityd role)──> mios-accountdb
                                                          │         (credstore: sealed OS hashes)     ^  (Network=none)
                                                          │                                           │ UDS scram (mons_broker)
                                                          └─ signs manifests (realm key) ─────────────┘
                                                                   │
            transport-agnostic, signed + per-node encrypted        v
            (mesh HTTPS pull, file spool, virtio-serial)   mios-account-agent fetch (unpriv, net)
                                                                   │ spool dir (0700)
                                                                   v
                                                           mios-account-agent apply (priv, PrivateNetwork)
                                                             -> /run/userdb, authorized keys cache, polkit,
                                                                group allow-list, per-role slice floors
                                                           MiOS-Xbox: mios-account-agent.exe via virtio-serial
```

| Edge | Credential | Transport | Notes |
|---|---|---|---|
| client → gateway (native, web) | room ticket (Ed25519, ≤ 15 min, `aud`, `epoch`) obtained with a device key, passkey or ed25519-sk proof | TLS 1.3. The realm CA is pinned in the client, from the realm invite. | Authenticate before any per-client state is allocated (M2). |
| phone SSH → gateway | SSH public key registered on the account | SSH, implemented in the gateway | No PAM/shell in the game transport; the same identity may separately hold scoped standard OS logon grants (D10). |
| local seat → gateway | `SO_PEERCRED` uid mapped to an account (only for projected `os.login` accounts) plus a ticket | UDS `/run/mios/mons/gateway.sock` | A uid without a mapping gets guest-seat policy (Q6). |
| gateway → room | per-room capability (random 256-bit, held only by the gateway and the supervisor) | UDS bind-mounted into the room | The room never sees tickets or account secrets. Its world is `{seat_id, account_uuid, display}`. |
| room → broker | the same room capability | UDS | The broker maps the capability to the room and allows only that room's pets. |
| broker → accountdb | `mons_broker` scram password delivered with `LoadCredential=` | UDS `/run/mios/accountdb/.s.PGSQL.*`, socket dir 0750 | Only `EXECUTE` on `mons_*` functions. |
| identityd → accountdb | `identityd` scram password via `LoadCredential=` | UDS | Only `EXECUTE` on `ident_*` functions. |
| gateway → identityd | none. The gateway is a relay: it forwards challenge and response and receives signed tickets. | UDS, with the peer uid checked by identityd | The gateway cannot mint tickets. |
| identityd → agent | manifest signed with the realm key (Ed25519). Secret fields are sealed to the node's X25519 key. | Any transport. Integrity comes from the signature. | Version is monotonic and `expires_at` is mandatory. |
| agent apply → OS | root, with a narrowed capability set | local | Re-validates against the SSOT. Fails closed. Never writes back. |

---

## 3. Identity backbone

### 3.1 What exists today, and why it cannot be the backbone
The repo already treats Postgres as the account source (ACCT-01..04, tasks 151-154, all recorded `completed`). As built, though, the account plane is a fleet-wide escalation path:
- The DB superuser has a vendor password (`mios-pgvector.container:14-15`) in a `Network=host` pod (`mios-ai.pod:21`), and loopback logins use `trust` (`mios-pg-query:14`).
- The schema has no roles and no `GRANT` statements.
- `mios-account-sync` runs as root, unsandboxed (`mios.toml:1645-1664`). It executes row contents, including `is_admin`, `groups`, `gid`, `shell` and `home_dir` (`mios-account-sync:101-117`; the shell falls back to `/bin/bash` at `:114`). It builds SQL with f-strings (`:173`, `:180`) and writes shadow hashes back to the global record (security C4).
- NOTIFY payloads carry password hashes, including NT hashes (`schema-init.sql:1305`, `:1324`).

The full chain is in `critique-security.md` C1-C5. D14 makes fixing it Phase P0.

### 3.2 One account, many grants
An **account** is `{uuid, realm, name, display, status, epoch, source}`, where `source` is `app`, `ssot` or `federated`. A **grant** is `{account, capability, scope, granted_by, approved_by, expires_at, created_at}`. Capabilities:

| Capability | Scope | Projects to | Minimum credential | Approval |
|---|---|---|---|---|
| `mons.play` | realm | game tickets | device key or passkey | self (requires an invite, M1) |
| `mons.host_room` | realm or node | room creation quota | device key or passkey | `accounts.admin` |
| `mons.moderate` | realm | kick/mute in any room | device key + step-up | `accounts.admin` |
| `client.native` / `client.web` / `client.ssh` / `client.windows` | realm | enrolled device keys, SSH game keys | the device itself | self, within an invite's allowance |
| `remote.ssh:<node>` | node | `AuthorizedKeysCommand` cache, group `mios-remote-ssh` | SSH key | `accounts.admin` |
| `remote.rdp:<node>` | node | group `mios-rdp`, already gated by `mios-rdp-priv` (`src/mios-rdp-server/crates/mios-rdp-priv/src/main.rs:1-8`) | OS password | `accounts.admin` |
| `remote.portal` | realm | per-user portal tokens (replaces the single shared password, H9) | passkey | `accounts.admin` |
| `vm.console:<vm>` / `vm.manage:<vm>` | VM | polkit rule fragments that name the libvirt domain. Never the `libvirt` group. | passkey or OS password; `manage` needs FIDO2 | `accounts.admin` |
| `os.login.linux:<node\|nodeset>` | node | userdb record (uid from a tier range, allow-listed shell and groups) | OS password and/or SSH key | `accounts.admin` |
| `os.admin.linux:<node>` | node | `wheel` on that node only | FIDO2 step-up | `fleet.admin` |
| `os.login.windows:<vm>` / `os.admin.windows:<vm>` | VM | MiOS-Xbox local account in the agent namespace / Administrators | OS password; admin needs FIDO2 | `accounts.admin` / `fleet.admin` |
| `node.login:<blade>` / `node.manage:<blade>` | Blade | Blade host userdb; Cockpit; `wheel` on that Blade; k3s RBAC binding; Ceph dashboard role | FIDO2 | `fleet.admin` |
| `mesh.join` | realm | Headscale user, tag `tag:mon-client` or `tag:workstation` | device key | `accounts.admin` |
| `accounts.admin` | realm | identityd admin API | FIDO2 | `fleet.admin` |
| `fleet.admin` | realm | everything above | FIDO2, with an SSOT-declared bootstrap | SSOT only (host tier) |

Roles are declared in SSOT (`[accounts.roles.<id>]`, Appendix A). The vendor ships policy-only roles: `player`, `member`, `operator` and `admin`. A role is a grant template plus allow-lists: allowed groups, shells, memory floor and uid tier. Approving a grant re-validates it against the role ceiling, so a `player` role can never carry `wheel`.

### 3.3 Where the accounts live, and why there
`mios-accountdb` is a new Quadlet that reuses `docker.io/pgvector/pgvector:pg18`. That image is already baked and bound, so no new image is fetched (Laws 3 and 12).
- **Network.** `Network=none` and outside every pod. The socket directory `/run/mios/accountdb` is bind-mounted, mode 0750, group `mios-accountdb-clients`. This adds a filesystem gate on top of scram.
- **Auth.** `pg_hba.conf` is generated (Law 8) and holds exactly one line: `local all all scram-sha-256`. `listen_addresses=''`.
- **Bootstrap secret.** Init reads the superuser password from a Podman `Secret=…,type=mount` through `POSTGRES_PASSWORD_FILE`, so it never enters the environment (Law 11). The superuser exists only for the migrator oneshot (`mios-accountdb-migrate.service`).
- **Service user.** `User=`/`Group=` is a new `mios-accountdb` service uid from sysusers, with `Delegate=yes` (Law 6). It needs no `privileged_quadlets` slot, and `max_privileged_root = 13` (`mios.toml:2846`) stays untouched.
- **No `install.env`.** It does not import `EnvironmentFile=/etc/mios/install.env`, unlike `mios-pgvector.container:18` (security C3, H8).

Why not a schema inside `mios-pgvector`? Its superuser is reachable from roughly 25 host-network containers through loopback `trust`. Re-hardening it would break the agent plane (`mios-pg-query` depends on `trust`), and that work belongs to another lane (Appendix B, H-1). A second, small cluster costs an estimated 30-60 MiB RSS idle, with `shared_buffers` sized by SSOT. In exchange it gives a clean authorization boundary.

### 3.4 Schema sketch (`mios_identity`)
- **`account`** columns: `uuid pk`, `realm text`, `name text`, `display text`, `status enum(active, disabled, pending)`, `epoch bigint`, `source enum`, `created_at`.
  - `name` must match the SSOT grammar (default `^[a-z_][a-z0-9_-]{0,30}$`) and must not be in the reserved list, checked by `CHECK` and by a trigger.
  - `unique(realm, name)`.
- **`credential`** columns: `id`, `account`, `type enum(device_key, passkey, ssh_key, ssh_sk_key, fido2)`, `public_material`, `label`, `created_at`, `last_used_at`, `revoked_at`.
  - Public material only. There is **no hash column of any kind**.
- **`os_secret_ref`** columns: `account`, `version`, `credstore_id`, `set_at`.
  - A reference to the sealed OS-password hash (§3.9). The hash itself is not here.
- **`grant`**: as in §3.2. `CHECK (capability IN <SSOT list>)`. Approval is mandatory for every capability other than `mons.play` and `client.*`.
- **`node`** columns: `node_id uuid`, `kind enum(blade, guest, xbox, workstation)`, `parent_blade`, `sign_pubkey`, `seal_pubkey`, `enrolled_by`, `enrolled_at`, `status`.
- **`uid_alloc`** columns: `realm`, `tier`, `next`.
  - Ranges come from SSOT, so `CHECK uid BETWEEN tier_min AND tier_max` holds, and 0 and anything below 1000 are impossible.
  - A uid is allocated lazily, only when the first `os.login.linux` grant is approved (M1).
- **`invite`** columns: `hmac`, `role`, `max_uses`, `uses`, `expires_at`, `issued_by`.
  - Only the HMAC of the code is stored, never the code (M1, M5).
- **`manifest`** columns: `node_id`, `version`, `issued_at`, `expires_at`, `digest`. The signature lives with the file, not in the row.
- **`audit`**: append-only, enforced by a trigger that refuses `UPDATE` and `DELETE`.
- **NOTIFY.** If any `NOTIFY` is used, its payload is only `{table, id, version}`.

`mios_mons`: `pet(id, owner_account, state jsonb CHECK octet_length ≤ 65536, schema int, version bigint, provenance enum(room, local_import), updated_at)`, `room(id, owner_account, policy jsonb, placement, created_at, last_active_at)`, `room_member`, `room_event` (append-only, bounded by a retention policy) and `leaderboard`, which counts only pets with `provenance = room`.

### 3.5 Authorization model in the database
- Roles: `identityd`, `mons_broker` and `ident_audit_ro`. All are `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`.
- Every write goes through a `SECURITY DEFINER` function with `SET search_path = pg_catalog, mios_identity`. Each function re-checks the SSOT-projected constraints, which a generated table `policy_snapshot` holds (Law 8).
- `mons_broker` cannot read `mios_identity` at all. It sees accounts only through `mons_account_view(uuid, display, status)`.
- RLS on `mios_mons.pet` is keyed to the room capability that the broker sets inside a definer function, never from a client flag. There is no `IS NULL` escape (security H3).

### 3.6 Projections, one per surface

| Surface | Projection (by `mios-account-agent apply` unless stated) | Delivered to |
|---|---|---|
| Linux OS logon | `/run/userdb/<name>.user` (public) and `.user-privileged` (yescrypt hash, created 0600 before the write and renamed atomically, written by `openat`). The record carries `mios.account_uuid`. Shell, groups and home come from the role allow-list. Home is `/var/home/<name>`. Stale records are deleted. | nodes in the grant scope |
| SSH remote | an `AuthorizedKeysCommand` helper (Rust, reads the agent's 0640 cache) and group `mios-remote-ssh`, plus an `AllowGroups` drop-in in the operator's sshd (generated) | nodes in scope |
| RDP | group `mios-rdp`, which `mios-rdp-priv` already enforces | nodes in scope |
| Portal | identityd issues per-user portal tokens. The portal checks them against identityd's public key and the epoch feed, which replaces the single-password HMAC token (`portal.py:124-147`, per critique-security H2/H9). | the portal host |
| VM | polkit `.rules` fragments in `/etc/polkit-1/rules.d/50-mios-vm-<uuid>.rules` that allow `org.libvirt.api.domain.*` for the named domain only. Console access goes through the gateway or Guacamole. | the VM's host Blade or node |
| Windows (MiOS-Xbox) | `mios-account-agent.exe` (§3.10) | that VM |
| Blade/Node management | the Blade host's userdb, plus `wheel` on that Blade only, Cockpit access, a k3s `ClusterRoleBinding` rendered from the grant, and a Ceph dashboard role | **that Blade's agent only** (D13) |
| Mesh | identityd's Headscale adapter creates the user and preauth keys (stored as HMAC) and tags the device per grant | the Headscale coordinator |
| LLDAP (optional) | a downstream projection for web apps such as Forgejo and Guacamole, fed from manifests. LLDAP is never a source. | the LLDAP host |

### 3.7 Provisioning and deprovisioning
- **Provisioning a grant.** Approval → identityd bumps the affected nodes' manifest versions → agents fetch and apply within the SLA → apply reports `{node, version, result}` back through `fetch` as a signed receipt.
- **Disabling an account.** identityd bumps `epoch`, sets `status = disabled` and pushes the epoch feed to every gateway and the portal, which drops sessions immediately. The next manifest marks the account `locked: true`. The agent then deletes `authorized_keys` cache entries, runs `loginctl terminate-user <uid>`, removes polkit fragments, and the room supervisor evicts the account's seats.
- **Deleting an account.** It is disabled first. After an SSOT retention window, the userdb record, home (archived or deleted by policy) and pets go.
- **Negative regression test (security H2).** After a disable, login, SSH-key login, RDP, portal and room join all fail on every online node within the SLA.

### 3.8 The per-node agent (`mios-account-agent`, Rust, static)
- **`fetch`** runs as the unprivileged user `mios-account-fetch`. It pulls `manifest-<node>.json` and its `.sig` from identityd over the mesh, or reads them from a spool or virtio-serial port. It writes them to `/var/lib/mios/account-agent/spool` (0700), and a `.path` unit triggers `apply`.
- **`apply`** runs as root with these unit settings:
  - `ProtectSystem=strict`
  - `ReadWritePaths=/run/userdb /var/home /etc/polkit-1/rules.d /var/lib/mios/account-agent`
  - `CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER CAP_DAC_OVERRIDE`
  - `NoNewPrivileges=yes`, `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`, `SystemCallFilter=@system-service`
  - absolute binary paths only

  For every manifest, `apply`:
  1. verifies the signature against the realm key in the host SSOT, the node id, a version greater than the last applied one, and an `expires_at` in the future;
  2. opens sealed fields with the node's X25519 key (a `LoadCredential=` systemd credential);
  3. re-validates every record: uid inside the tier range, gid equal to the per-user group, groups inside the role allow-list, shell inside the shell allow-list, home equal to `/var/home/<name>`, name matching the grammar;
  4. writes the result atomically;
  5. never edits `/etc/shadow`, never stores hashes in a state file (it keeps `{name: version}` only), never logs a hash and never writes to the database.

  On divergence it alerts and re-applies (security C4).
- **Break-glass.** A local account in `/etc/passwd` that no manifest, SSOT tier or database state can modify or lock. `apply` refuses any record with the same name or uid. The repo's own incident, where a 0-byte SSOT made every login fail, is the reason.
- **Stale manifests.** After `expires_at`, *elevated* grants are dropped: `wheel`, remote, `node.manage` and `vm.manage`. `os.login` for accounts whose role is `operator` is kept. This trades security against the 0-byte-SSOT failure mode, and the operator decides it (Q4).

### 3.9 Secrets out of the environment (Law 11)
- **No secret appears** in `install.env`, unit `Environment=`, argv, NOTIFY, logs, state files or Postgres columns.
- **Database role passwords and node seal keys** are delivered with `LoadCredential=`, as systemd credentials encrypted with `systemd-creds` (TPM2 where the guest has a vTPM, the host key otherwise).
- **The realm signing key** sits in identityd's credential store, `/var/lib/mios/identity/credstore` (0700, the identityd uid). Where it lives in future is Q7.
- **OS-password hashes** sit in that same store, sealed. They reach nodes only sealed per node, and land only in userdb `*.user-privileged`. Law 11 today names only `/etc/shadow` and `/etc/mios/secrets.env`, so this design asks root for an explicit amendment that adds two stores: the identity credstore and userdb privileged records (Q3). Until the amendment lands, OS-password logons stay disabled for non-break-glass accounts, and the SSH-key and FIDO2 paths work.

### 3.10 Windows (MiOS-Xbox)
- **Players may receive a standard Windows/MiOS-Xbox account under root-owned enrollment policy**, using the same password as Linux. VM console and management grants remain separate.
- **`os.login.windows:<vm>` is projected by `mios-account-agent.exe`.** It is the Windows target build of the same Rust crate (Law 14: no new PowerShell-as-program), code-signed under `[security.powershell_signing]`.
  - It reads signed manifests from a dedicated virtio-serial port declared in the VM definition. WinRM is not used, and the guest gets no new network listener.
  - It manages only accounts with the SSOT name prefix and a description tag.
  - It touches Administrators only for `os.admin.windows`, under a `fleet.admin`-approved entitlement.
  - When the user sets or changes their OS password, identityd seals the **plaintext** to the VM agent's X25519 key. The agent sets the password with `NetUserSetInfo`, zeroizes it and acknowledges. identityd then deletes the sealed blob, which has an SSOT TTL. No application-owned NT hash is stored or replicated (security C2); native platform storage is not mirrored into MiOS databases.
- **Serial log.** The agent logs to the guest serial port, as `MiOS-Xbox.Provision.ps1` does.
- **Hazard.** Task 153 (DB→SAM sync) is recorded `completed`, yet the tree has no runtime consumer of `account_sync` (Appendix B, H-6).

### 3.11 OS logons and the memory floors
One account password works for permitted Linux and Windows/MiOS-Xbox OS logons. Each node validates signed scope, identifier ranges, group allow-lists, home and shell policy before applying a standard account. Database is_admin flags never authorize elevated membership. Operator/recovery identities stay outside bulk player synchronization.

Game SSH/web seats are not OS sessions. Real OS logons require per-user accounting: replace blanket10GiB user-slice and6GiB user-service protection with measured role budgets and admission before multi-user enablement (T20). Verify simultaneous Linux/Windows logons and preserve existing sessions when admission declines.

Native Windows logons use platform-managed verifiers. Forbid application-owned NT-hash storage/replication rather than promising no NT-derived value exists anywhere in Windows. See [Microsoft password storage documentation](https://learn.microsoft.com/en-us/windows-server/security/kerberos/passwords-technical-overview). Cross-OS password rotation, stale-node recovery and sealed-delivery expiry need disposable Linux/Windows verification.

---

## 4. Credential registration

### 4.1 Node kinds and their authority

| Kind | Example | Holds | Can approve grants? |
|---|---|---|---|
| Identity primary | the guest-plane node hosting `mios-identityd` and `mios-accountdb` | realm signing key, credstore | yes, through `accounts.admin` and `fleet.admin` principals |
| Blade (MiOS-Metal host) | bare metal | its own agent, Blade-scoped grants | no |
| Hosted guest node | the MiOS OCI image as a NIC-less guest | its own agent | no |
| MiOS-Xbox | Windows VM | Windows agent | no |
| Workstation (standalone) | a single MiOS PC; it is its own realm and its own identity primary | everything | yes, locally |
| Client device | phone, Windows `mios-launch`, browser | device key or passkey | no |

### 4.2 Declarative registration through the SSOT (host tier only)
```toml
# /etc/mios/mios.d/50-accounts.toml  (root-owned host tier; user tier is IGNORED for [accounts.*])
[[accounts.users]]
name  = "alice"
role  = "operator"
ssh_keys = ["sk-ssh-ed25519@openssh.com AAAA..."]       # public material only
fido2 = ["credential-id:..."]                            # public material only
os_password_ref = "credstore:accounts/alice/os"          # a REFERENCE; the value is set at install by mios-bootstrap
grants = ["os.login.linux:fleet", "remote.rdp:blade-a"]
```
- identityd reconciles `[[accounts.users]]` with `source = ssot`. The apps cannot change SSOT-sourced grants. Removing an entry disables the account.
- The vendor tier ships **schema and policy only**: roles, ranges, allow-lists, limits. It ships no users and no credential values.
- The user tier (`~/.config/mios`) is ignored by every system-scope consumer for any `accounts.*` key, as an explicit carve-out from Law 13 tiering that both resolvers enforce (security M8).
- `fleet.admin` can only be bootstrapped from the SSOT (host tier), never from an app.

### 4.3 Registration through the MiOS-Mons game clients
The game TUI, game web/mobile client and Windows/MiOS-Xbox game client own registration. Rename usr/libexec/mios/mios-mon.py with its monitoring launcher/docs/consumers. It is not a game registration app.

1. Join an explicitly trusted realm with a bounded invite and pinned identity fingerprint; discovery does not establish trust.
2. Enroll one global account, a device credential/passkey and one user-chosen OS password over an authenticated secure channel. Native stores and node-scoped sealed delivery protect OS-specific verifiers. No password goes in TOML, application database rows, argv, logs or notifications.
3. Root-owned standard-player enrollment policy identifies permitted Linux and Windows/MiOS-Xbox nodes. Both use the same password. Enrollment is not authority to manage a VM, Blade, network or another account.
4. Default groups come from the standard-user allow-list, never the operator's elevated groups. Administrative grants require an independent decision and step-up, including requests made from the game.
5. Track password-version delivery per node; failed/stale nodes cannot count as synchronized. Rotation/revocation preserve operator recovery access.

Quotas, enrollment scope, roles, credential references and admission are SSOT-owned. Unresolved credential-store/Law11 questions remain implementation gates.

### 4.4 Enrolling Blades, hosted guests and MiOS-Xbox
1. At first boot, each node's agent generates a sign/seal keypair with the private half as a systemd credential, then shows an enrollment code: the key fingerprint plus a short code.
2. An admin approves the node in identityd. A one-time enrollment token stored as an HMAC can stand in for interactive approval (for PXE, `mios-pxe-hub`).
3. The record carries `kind` and `parent_blade`. A `guest` node can never receive Blade-scoped grants, which keeps hardware roles unclaimable by guests.
4. MiOS-Xbox enrolls through the virtio-serial channel, with approval by the host's `vm.manage` holder.

### 4.5 Trust within a realm and between realms
- **Inside a realm.** The realm key signs manifests and tickets. Rotation is a signed key-transition record, accepted when the old key signed it.
- **Between realms (federation, hosted).** The host SSOT declares `[[accounts.federation.trusted]] {realm, pubkey, cap_ceiling = ["mons.play"]}`.
  - Foreign tickets are accepted only with `aud` naming this realm's gateway. The principal is `user@realm`, gets no uid and no OS projection, and is capped at the realm's `cap_ceiling`.
  - Revocation uses signed revocation lists plus ≤ 15 min ticket lifetimes.
  - Rooms with federated seats are separate containers in any case (D6).
  - mDNS, `[a2a]` CIDR sweeps and every other discovery mechanism never create trust (security H7).
  - P11 is gated on `[security].api_require_auth = true` and `principal_bind_mode = "enforce"`.
- **Hosted.** "Hosted" means a realm that hosts rooms for another realm's users. It is plain federation with `cap_ceiling = ["mons.play"]` and a room quota per foreign realm.
- **Identifiers.** Each fleet generates its own SID prefix at realm init, in place of the fixed prefix at `mios-ai-firstboot:435`. Uid ranges are realm-local, and remote uids and SIDs are never trusted (security H6).

### 4.6 Revocation
§3.7 covers accounts. Devices: revoking a credential row bumps the epoch, and tickets minted with that credential die at the next feed push. Nodes: a disabled node's manifests stop and its seal key is dropped from future sealed fields. Realms: removing the federation entry plus a revocation list.

---

## 5. Rooms as minimal containers

### 5.1 Components on one host

| Component | Runs as | Form | Network |
|---|---|---|---|
| `mios-accountdb` | `mios-accountdb` uid | system Quadlet | none (UDS) |
| `mios-identityd` | `mios-identityd` uid | system unit (static binary) | none (UDS). The gateway relays to it. |
| `mios-monsd gateway` | `mios-mons-gw` uid | system unit | the only listener: `[ports].mons_gateway` (TLS/WSS) and `[ports].mons_ssh` |
| `mios-monsd broker` | `mios-mons-broker` uid | system unit | none (UDS) |
| `mios-monsd supervisor` | `mios-mons-rooms` (lingering service account with its own subuid range) | user unit in that account's manager | none (UDS) |
| `mios-mons-room@<id>` | inside the userns, `User=65532` | user-scope Quadlet under `mios-mons-rooms` | `Network=none` |
| render seats | `mios-mons-render` uid | per-seat sandboxed process under a `mios-monsd presenter` unit | none. Gets a PTY from the presenter. |

### 5.2 Image
- `localhost/mios-mons-room`, built at bake by a new `automation/5x-bake-mons-room.sh`, following the precedent of `automation/54-bake-coderun-sandbox.sh`.
- The final stage is `FROM scratch` plus the static musl `mios-mons-room` (`tools/native/.cargo/config.toml:7`, `crt-static`) and `USER 65532`. There is no shell or libc in the image. This follows the distroless conformance pattern in `automation/98-drift-checks.sh:665-732`.
- The image is symlinked into `/usr/lib/bootc/bound-images.d/` (Law 3) and never pulled at runtime (Law 12).
- It is an application image, not a bootc image, so it follows the coderun `Dockerfile` precedent and Law 4 does not apply. Root confirms this (Appendix C, LO-R7).

### 5.3 The room unit
`/usr/share/containers/systemd/users/mios-mons-room@.container` is generated from `[mons.room]` (Law 8) and starts from `mios-coderun-sandbox@` (security H8):
- `[Unit] ConditionUser=mios-mons-rooms`, so other users' generators emit a unit that never starts.
- `User=65532`, `Group=65532`. It never needs `User=root`, so it takes no `privileged_quadlets` slot. Delegate follows the user-unit rule of `no_group_delegate`, and root records the exemption.
- `Network=none`, `ReadOnly=true`, `VolatileTmp=true`, `NoNewPrivileges=true`, `DropCapability=ALL`, `SecurityLabelType=container_t` with a per-instance MCS label, and a seccomp profile that is AF_UNIX-only.
- `Volume=%t/mios-mons/rooms/%i:/run/room:Z`. That directory holds only the room's UDS.
- `PodmanArgs=--memory=<mons.room.memory_mib>m --pids-limit=<mons.room.pids> --cpus=<mons.room.cpus>`, all from the SSOT.
- No `EnvironmentFile`, no Podman socket, no `$HOME`, no secrets.
- In addition, the binary applies its own `seccompiler` filter after init (no `execve`, no `socket(AF_INET*)`) and a Landlock rule set. It contains no process-spawning code at all (§6.2).

### 5.4 Gateway
- **Listeners.** It is the only network listener. It speaks TLS 1.3 (rustls). The certificate comes from the realm CA, and the key is a `LoadCredential=`.
- **Framing.** It serves the WSS endpoint and the static web client (§6.9). Framing is length-prefixed with an SSOT `max_message_bytes`.
- **Auth.** Handshake and auth have deadlines, and no per-client state is allocated before auth. Tickets are verified offline against the realm public key and the epoch feed.
- **Limits.** Per-IP, per-account and per-room connection caps (security M2).
- **Routing.** The gateway multiplexes seats onto a room's UDS and translates nothing. It enforces message type and rate per seat. It also runs the game SSH listener: public keys map to accounts through identityd, and each session gets a render seat.

### 5.5 Broker and supervisor
- **Supervisor.** It starts and stops `mios-mons-room@<id>` through the `mios-mons-rooms` user manager. It creates the room's runtime directory and capability, and hands the capability to the gateway and the broker over UDS, checking `SO_PEERCRED`.
- **Admission.** Before every start, the supervisor runs admission: free memory must exceed `[system.memory_admission].min_available_mb` (`mios.toml:19949`) plus the room budget, and the host's `max_rooms` must not be reached.
- **Broker.** The broker loads and saves pets with optimistic concurrency, appends room events and enforces quotas. It is the only holder of `mons_broker`.

### 5.6 Lifecycle
1. **Create.** A `mons.host_room` holder asks for a room. The broker records it, the supervisor starts the container, the room asks the broker for its members' pets, and the gateway starts routing.
2. **Run.** The room ticks at `tick_seconds` and checkpoints dirty pets to the broker every `autosave_seconds` (`mios.toml:20112`) and whenever a seat leaves.
3. **Idle.** After `[mons.room].idle_reap_s` with no seats, the room checkpoints and the supervisor stops it. A persistent room restarts on the next join, so a stopped room costs 0 CPU and 0 RAM.
4. **Drain.** On host shutdown or update, the supervisor sends `drain`. The room checkpoints and sends `bye{reason, retry_after}`. Clients reconnect.
5. **Crash.** systemd restarts the room with a bounded `StartLimit`. State is lost back to the last checkpoint, at most `autosave_seconds`.

### 5.7 Placement
- **Single host or standalone workstation.** Quadlets as above.
- **Fleet (P10).** Rooms become k3s Pods rendered from the same `[mons.room]` (Law 8): `runAsNonRoot`, `readOnlyRootFilesystem`, all capabilities dropped, a localhost seccomp profile, `automountServiceAccountToken: false`, no `hostNetwork`, and a NetworkPolicy that denies all traffic. Room sockets reach the gateway through a per-node gateway DaemonSet and a hostPath runtime directory.
- **Blocked until:**
  - task_934 lands, because three default Minis today form three k3s clusters (`mios.toml:3205-3212`);
  - task_935 lands for identity HA, because Pacemaker is unfenced;
  - task_933 lands for backups to Ceph.

  Until then the identity primary is a single node. Fleet nodes keep working from cached manifests, and rooms can run on any node with a local gateway that verifies realm tickets offline.

### 5.8 Ports and discovery
- **Ports.** Two new keys, `mons_gateway` and `mons_ssh`, in a new `[ports.categories.games]` band. Root allocates the base from the free range and `check_ports_bound` verifies it. Rooms have no ports.
- **Firewall.** `[firewall].open_ports` gains `mons_gateway` and `mons_ssh` only when `[mons].remote_enabled = true`.
- **Mesh.** Headscale gains `tag:mon-client` and `tag:mon-gateway`. The ACL allows `tag:mon-client → tag:mon-gateway:<mons ports>` and nothing else. Players never get `tag:guest` or `tag:workstation` (security H4).
- **Discovery.** Invites carry the address, and mDNS may *suggest* a LAN gateway, but trust always comes from the realm CA pinned by the invite.

### 5.9 How clients connect
- **Native.** `mios-tamagotchi --connect <invite-or-realm> [--room <id>]` reaches the gateway over TLS. Windows `mios-launch` embeds the same client.
- **Web.** The gateway serves a static page: a baked xterm.js plus a small Bun/TS shim. The page opens a WSS connection to a render seat. For players this replaces ttyd, which runs as the operator (`mios-ttyd-bash.service:21-22`, security H9).
- **Phone.** `ssh -p <mons_ssh> <name>@<gateway>` uses a registered key and gets a render seat. It is never an OS login.
- **Local.** `/run/mios/mons/gateway.sock`, for same-host seats and nested PiP sessions.

---

## 6. Game split

### 6.1 What the current code gives us
- **Rules.** `Game` (`game.rs:99-123`) is a serde struct of integer needs, coins, wins, stage counters and challenge state. `tick()` (`:223-262`), `care()` (`:264-331`) and `guess()` (`:333-360`) are deterministic and wall-clock-free. `validate()` and `validate_habitat()` bound everything (`:163-202`). These rules move unchanged into the authoritative room.
- **Movement.** Pet motion lives in `Ui`, not in `Game`: `position`, `destination`, `yaw`, `target_yaw` and `visual_rng` (`ui.rs:144-148`). `simulate_behavior()` draws destinations from a cosmetic xorshift and computes yaw with `f64::atan2` (`ui.rs:683-717`). Floating-point yaw on each client would drift between clients, so motion must move into the core, in integer form, and the server must decide it.
- **Pausing.** An open overlay pauses the simulation clock (`ui.rs:5171-5173`), and `paused` is a field of `Game`. In a room, a menu one player opens must not freeze everyone else's pet.
- **Randomness.** The memory-game pattern comes from an LCG kept in the save (`game.rs:309-311`). The LCG's state (`rng`) is serialized, so any client holding a snapshot can predict upcoming patterns.
- **Saves.** They are bounded (64 KiB, `save.rs:13`), versioned (schema 4, `save.rs:12`) and single-writer through an OS lock (`save.rs:97-130`).
- **Processes.** The client spawns host processes: `tmux` by `$PATH` (`dialogs.rs:573`), `/bin/sh` (`dialogs.rs:926`), `kill` (`dialogs.rs:484`, `input.rs:383`) and an arbitrary program in the native query path (`input.rs:308`).
- **Text.** `visible()` strips only `Cc` (`dialogs.rs:386-392`).

### 6.2 Crate split
- **`tools/native/mios-mons-core`** (lib). Holds `game`, `genetics`, `habitat`, `progression`, `characters` (data only), save envelope and validation, and a new **`motion`** module. `motion` uses integer fixed-point position, destination and speed, and integer yaw from a lookup table. It has no ratatui, crossterm, process or filesystem I/O beyond pure parse and serialize.
- **`tools/native/mios-tamagotchi`**. Becomes the client and the single-player game. It depends on core and keeps rendering, input, dialogs and the local save. Process-spawning features (native dialogs, controller query, tmux) sit behind a cargo feature that stays *on* for the local desktop build and is *off* in presenter mode.
- **`tools/native/mios-monsd`** (bin). Subcommands `gateway`, `broker`, `supervisor` and `presenter`. A second bin target, `mios-mons-room`, depends only on core and the protocol crate.
- **`tools/native/mios-mons-proto`** (lib). Framing, message types, validation, and generation of the protocol JSON Schema.
- **`tools/native/mios-identityd`** and **`tools/native/mios-account-agent`** (bins). The agent also has a Windows target.

### 6.3 The authoritative room
State is `Room {id, policy, tick, seats, pets: {pet_id → PetSlot{owner, game: Game, motion: Motion}}}`, held in one event loop on `epoll`. The loop wakes on a `timerfd` at `tick_seconds` and on socket readiness, and on nothing else. Each tick:
1. `game.tick()` runs for every present pet;
2. `motion` decisions run for every present pet;
3. the room emits deltas.

Intents are validated against `Rules` and answered with the static `&'static str` messages from `game.rs`, which are injection-free by construction. Room minigames, such as memory duels, draw from a room CSPRNG (`getrandom`). The pet's LCG stays for solo care so saves remain compatible. Snapshots on the wire use a `PetView` that omits `rng` and the genesis internals.

### 6.4 Thin client
- **Room mode.** In room mode the client keeps **all presentation**: camera, zoom, orbit, HUD, renderer, sprite style, menus, dialogs, reactions and expressions. It never simulates game state. It sends intents and applies snapshots and deltas.
- **Offline mode.** Offline single-player stays exactly as today (local `Game`, local save) and runs the same core code.
- **Overlays.** An open overlay never pauses the room. Pausing is a room policy (`[mons.room].pause = "owner_alone" | "never"`).

### 6.5 Wire protocol
- **Frames.** Each frame is a `u32` big-endian length followed by a UTF-8 JSON object with a `type` field.
- **Schema.** The protocol is declared as an OpenAI-format JSON Schema (global directive), generated from Rust types into `usr/share/mios/schemas/mons-protocol.json` and drift-checked (Law 8).
- **Messages.**
  - Client to room: `hello{proto, client_kind}`, `auth{ticket}`, `join{room}`, `intent{seq, kind, args}`, `chat{text}`, `pong`, `leave`.
  - Room to client: `welcome{seat, room_policy}`, `snapshot{tick, pets[PetView], seats}`, `delta{tick, changes}`, `keyframe{pet, from, to, start_tick, speed, yaw}`, `event{pet, message, expression}`, `presence`, `chat{from, text}`, `ping`, `error{code}`, `bye{reason, retry_after}`.
- **Ordering.** Every intent carries a monotonic `seq`, and the ack echoes it.
- **Limits.** Unknown fields are rejected (`deny_unknown_fields`, matching `save.rs`). Size and rate limits apply per message type.
- **Bandwidth (estimate).** A snapshot of 4 pets is under 4 KiB. A delta is under 200 B every 2 s. That is under 1 KiB/s per seat.

### 6.6 Movement without drift
- **The server decides.** It makes every wander decision on simulation ticks and emits `keyframe{from, to, start_tick, speed, yaw}`.
- **The client interpolates.** It computes position as a pure integer function of `(from, to, speed, frames_since(start_tick))` at its own animation cadence. Two clients that receive the same keyframe draw the same path whatever their frame rates.
- **Corrections.** Each keyframe is absolute, so a client that drops frames snaps to the next one, and error never accumulates.
- **Validation.** Yaw comes from a 16-entry integer table. Core tests assert that server and client positions match bit for bit across frame rates of 1, 7 and 60.

### 6.7 Per-client presentation
Camera, zoom (50-400 %), orbit, HUD mode, renderer, color depth, sprite style, dialog mode and the controller prompts from `mios-inputd` all stay per client and are never sent to the room. Reactions are client-side animations triggered by `event` messages.

### 6.8 Save model
- **In rooms.** The authoritative copy is `mios_mons.pet`, using the save-schema-4 envelope (`save.rs`). The broker writes it with a version check, so a lost update fails instead of overwriting silently. Pets are keyed by account UUID.
- **Offline.** Local saves stay for single-player.
- **Import.** `mons_import_pet` runs the same `validate()` server-side and sets `provenance = local_import`. Leaderboards exclude those pets (security M4).
- **Never.** A client never uploads state into a room. It sends intents only.

### 6.9 Render seats
- **What a seat is.** The presenter allocates a PTY pair. It runs the client (`mios-tamagotchi --connect --presenter`, with process spawning compiled out) on the slave inside a sandbox: a per-seat cgroup with memory, pids and CPU caps, plus seccomp and Landlock. It copies bytes between the master and the SSH or WSS channel. The client code runs unchanged.
- **CPU cost.** Seats are damage-driven (§7.3). When the room is idle, a seat runs at `[mons.render].idle_fps`, and the voxel room backdrop is cached per (camera, size, renderer). Animating seats run at `animation_frame_ms`, and idle seats drop to `idle_fps`, in line with the desktop frame-rate policy.
- **Sharing.** Seats that show the same room with the same (camera, size, renderer) may share one rendered frame. This is an optimisation for P6+, measured before it is adopted.

### 6.10 Local multiplayer
- **L1, several terminals on one host.** Each terminal runs a client against `/run/mios/mons/gateway.sock`. A seat is authenticated by `SO_PEERCRED` → `mios.account_uuid` (from the userdb record) for projected accounts, or by an account login.
- **L2, one terminal with several controllers.** One client process hosts N local seats. `mios-inputd` already assigns pads per focused app with route isolation (commits `7eeae0dcc`, `fffa32070`). The client maps pad *k* to seat *k*. Each seat shows its own HUD strip, and the camera is shared or split by SSOT policy.
- **L3, nested PiP sessions.** `mios-gen nested-session` already models seat and user identity and assigned input devices (`mios.toml:19941-19944`). A nested session is an L1 seat.
- **Guest seats.** A seat without an account plays under the host account's room policy and keeps no progress (Q6).

### 6.11 Text safety
- **Server.** The room and identityd normalize display names, pet names and chat: NFKC; remove `Cc`, `Cf`, bidi controls and zero-width characters; cap lengths; enforce display-name uniqueness on a confusable skeleton.
- **Client.** `visible()` also strips `Cf` and bidi, as defense in depth (security M3).
- **Never.** Raw bytes are never relayed between clients.

---

### 6.12 Required v1 social gameplay
V1 includes visits, permissioned co-care, memory duels, atomic trading, server-provenance leaderboards and hotel-style social rooms of varying sizes with games/socializing. These are delivery requirements, not post-v1 stretch goals. SSOT bounds room dimensions, furnishing budgets and seeded themes. Authoritative simulation owns wandering/growth/RNG; controls navigate menus/HUDs/cameras and never drive Mon locomotion.

Validate membership and interaction rights on every intent. Care needs owner/room consent. Trades validate both owners and commit all transfers atomically with replay/double-spend protection. Award duels once. Imported/local-editable pets cannot enter authoritative leaderboards. Sanitize chat/thoughts/dialogs and retain inline tiny/remote fallbacks. Each feature needs positive, unauthorized/replay negative, reconnect and concurrent-client tests. T27 owns the v1 delivery gate.

## 7. Scale

### 7.1 Where CPU goes today
- **Every iteration redraws.** The single-player loop calls `terminal.draw()` on *every* iteration (`ui.rs:5101-5104`). It then waits at most `min(tick_ms, animation_frame_ms)`, which is 100 ms with the vendor values (`mios.toml:20077`, `:20103`). The same pattern is at `ui.rs:1876-1878` on `main` `f576f22f1`. So each instance renders the CPU voxel scene about 10 times a second even when nothing changes, and once more after every input event.
- **Every viewer is a full game.** Served through tmux, ttyd or phone SSH, each viewer runs its own complete process. Host CPU therefore grows with viewers × voxel cost.
- **Estimate.** A voxel frame on a 120×40 terminal at half-block resolution samples about 9.6k pixels; braille samples about 4× that. At a few hundred ns per sample, that is 3-15 ms per frame, or roughly 3-15 % of a core per idle instance. This is unmeasured. §7.4 measures it first, and P1 sets the baseline that every budget is ratcheted against.

### 7.2 Budgets (acceptance criteria, confirmed by P1 measurements)

| ID | Budget | Gate |
|---|---|---|
| B1 | Idle room, no seats: ≤ 1 wakeup per `tick_seconds`; ≤ 0.05 % of one core averaged over 60 s; room RSS ≤ 8 MiB; container overhead (conmon) reported; total ≤ 16 MiB per idle room | P3, P5 |
| B2 | Active room, 4 native seats: room ≤ 0.5 % core; gateway ≤ 0.05 % core per idle connection | P5 |
| B3 | One host serves 200 rooms and 400 native seats with gateway + broker + rooms ≤ 1 core and ≤ 4 GiB RSS | P5 |
| B4 | Render seat at 120×40 voxel: static scene ≤ 2 % core; animating ≤ baseline/2 at the same cadence | P6 |
| B5 | Single-player local client, idle: ≥ 50 % less CPU than the P1 baseline, with no visible animation regression in snapshot tests | P1 |
| B6 | Admission: refuses a room start below `min_available_mb` + room budget; refuses above `[mons.host].max_rooms` | P5 |
| B7 | Stopped (reaped) rooms: 0 processes, 0 CPU | P5 |

### 7.3 How one host serves many
1. Rooms are headless, event-driven and tick-paced.
2. Rendering moves to client machines.
3. Render seats are damage-driven: redraw only on a state delta, an animation frame of a moving or reacting entity, a resize or input, and otherwise at `idle_fps`. The static backdrop is cached.
4. Idle rooms are reaped.
5. Rooms use UDS with no per-room ports, so there are no netns veth pairs or `pasta` processes.
6. Gateway and broker are single processes multiplexing all rooms.
7. Admission is bound to `MemAvailable`, not load average, as the agent-load memory note requires.
8. A shard mode (`[mons.room].rooms_per_container > 1`) exists in the binary but defaults to 1. It is enabled only if B1 measurements show conmon overhead dominates (Q9), and only for rooms of one owner and one trust class (security H8).

### 7.4 Measurement harness
`mios-monsd bench` is a Rust bin. It spawns N synthetic seats over UDS against M rooms and reads `cpu.stat usage_usec` and `memory.current` from the rooms' cgroup subtree, reporting p50 and p99 per second. It is headless, needs no network, and in CI it runs N=8, M=4. A full host run must follow the agent load limits:
- at most 4 lanes in total;
- check `MemAvailable` first;
- one nested session per lane;
- stop by PID.

A client render benchmark drives `ui.draw` on a `TestBackend` at fixed sizes and reports ns per frame and frames per minute while idle.

---

## 8. Laws compliance

| Law | How this design complies |
|---|---|
| 1 USR-OVER-ETC | Vendor policy lives in `[accounts]` and `[mons]` in `/usr/share/mios/mios.toml` or `/usr/lib/mios/mios.d`. Host accounts and federation go in `/etc/mios/mios.d`. Units and Quadlets go under `/usr`. |
| 2 NO-MKDIR-IN-VAR | `/var/lib/mios/{accountdb,identity,account-agent,mons}` are declared in `usr/lib/tmpfiles.d/mios-mons.conf`. |
| 3 BOUND-IMAGES | `mios-accountdb` reuses the bound pgvector image. `localhost/mios-mons-room` is bound like coderun. |
| 4 BOOTC-CONTAINER-LINT | No new bootc `Containerfile`. The room image is an app `Dockerfile`, following the coderun precedent (Appendix C, LO-R7). |
| 5 UNIFIED-AI-REDIRECTS | No AI calls. Any future Mon dialogue goes through `MIOS_AI_ENDPOINT`. No vendor names. |
| 6 UNPRIVILEGED-QUADLETS | accountdb and rooms declare non-root `User=`/`Group=`. No new `privileged_quadlets.root` entry. |
| 7 NO-HARDCODE | Ports, ranges, TTLs, limits, grammars, allow-lists and fps all come from the SSOT. No default credentials. `or "<literal>"` fallbacks are removed (P0b). |
| 8 SSOT-PROJECTION | Generated and drift-checked: Quadlets, units, `pg_hba`, role grants and the `policy_snapshot`, Headscale tags, the protocol schema, and k8s manifests in P10. |
| 9 ONE-CANONICAL-NAME | New `MIOS_ACCOUNTS_*` and `MIOS_MONS_*` names are emitted by the resolver, and none of them carries a secret. |
| 10 BARE-SAFE-ENV | The new services do not read `install.env`. They use the native resolver (`mios-resolver`). |
| 11 SECRETS-NEVER-IN-ENV | Secrets arrive only by `LoadCredential=` or Podman `Secret` mounts. Postgres holds no hashes. Stores outside shadow and `secrets.env` need the amendment in Q3. |
| 12 BAKE-NOT-FETCH | Binaries, room image and xterm.js are baked, and cargo deps are vendored for offline builds. Firstboot degrades open: with no identity, single-player and break-glass still work. |
| 13 NATIVE-DROPINS | Tier-major resolution is unchanged, with one documented carve-out: system consumers ignore user-tier `accounts.*`. Both resolvers implement it and a parity test covers it. |
| 14 TARGET-LANGUAGES | All new daemons and agents, the Windows agent included, are Rust static binaries. The portal page is Bun/TS. Three Python daemons are retired. No new Python, bash or PowerShell programs. |
| 15 DOUBLE-REPO-TRIPLE-CHECK | `mios-bootstrap` must generate per-surface credentials, stop writing the vendor default, and run realm init. Every phase touching install checks both repos. |
| 16 ONE-TEMPLATE-PER-TYPE | Crates, Quadlets, units and the JSON Schema are scaffolded with `mios new rust-crate`, `quadlet-container`, `systemd-unit` and `json-schema`. Those templates exist in `usr/share/mios/templates/`. |
| Blade invariant | Identity, gateway and rooms are guest-plane. Ingress and WAN exposure belong to the Blade (task_931). Blade-scoped grants go to Blade agents only. Guests are never mesh peers (task_930). |
| SSOT credentials, no defaults | The SSOT holds policy and credential *references*. Values are generated at install or firstboot into credstores. A missing value fails closed. |

---

## 9. Phased plan (detail and acceptance criteria in `TASKS-PROPOSAL.md`)

| Phase | Name | Unlocks | Hard dependencies |
|---|---|---|---|
| P0a | Containment of the account plane (no behaviour change) | safe base | none |
| P0b | Cut the default-credential chain (both repos) | no-default rule | P0a, mios-bootstrap |
| P1 | Measure, and fix the client redraw loop | B5, baseline | none |
| P2 | `mios-mons-core` split, server-decided integer motion | authoritative room | P1 |
| P3 | Protocol, headless room, local multiplayer over UDS | G2 | P2 |
| P4 | `mios-accountdb` and `mios-identityd`, invites, device keys, tickets | G5 and G6 foundation | P0a |
| P5 | Room containers, supervisor, gateway, broker, persistence, admission | G1, G3, G4 | P3, P4 |
| P6 | Render seats (web, phone SSH), epoch feed | thin viewers, ttyd retired for players | P5 |
| P7 | `mios-account-agent` (Linux), signed manifests, cutover from `mios-account-sync` | OS projection | P4, P0a |
| P8 | Remote, VM, portal and Headscale projections; Blade/Node management with FIDO2 | the grant surfaces | P7 |
| P9 | MiOS-Xbox agent | Windows OS accounts | P7 |
| P10 | Fleet placement (k3s), identity HA and backups, multi-Blade manifests | fleets | P5, P7, tasks 931/933/934/935 |
| P11 | Federation and hosted realms | federated play | P5, P8, security-mode enforcement |

Owner boundaries:
- **Game lane:** `tools/native/mios-tamagotchi`, `mios-mons-core`, `mios-mons-proto`, `mios-monsd`.
- **Identity lane:** `mios-identityd`, `mios-account-agent`, the accountdb schema files.
- **Root:** `usr/share/mios/mios.toml`, `CLAUDE.md`/`AGENTS.md` law text, `[security.*]`, `[firewall]`, `[ports]`, sysusers, the Headscale policy, the memory-floor drop-ins, the generators, `tasks.jsonl`/`TASKS.md`, the `mios-bootstrap` repo and every push to `main`. Lanes propose SSOT fragments and root applies them in sequence.

---

## 10. Operator answers and remaining questions

| # | Question | Answer or remaining proposal |
|---|---|---|
| Q1 | Game clients or monitor? | **Answered:** game clients own registration; rename the separate mios-mon.py infrastructure monitor. |
| Q2 | Cross-OS password and OS accounts? | **Answered:** one password for permitted Linux/Windows/MiOS-Xbox logons; protected per-OS verifiers, sealed delivery and standard-user groups. |
| Q3 | Will you amend Law 11 to allow two more secret stores: the identity credstore and userdb `*.user-privileged`? | Yes. Until then, OS logons use SSH keys or FIDO2 only. |
| Q4 | When a node's manifest goes stale, drop elevated grants only, or lock every non-break-glass account? | Drop elevated grants only. Operators keep login. |
| Q5 | V1 gameplay? | **Answered:** visits, co-care, memory duels, trading, leaderboards, and hotel-style social rooms of varying sizes with games/socializing. |
| Q6 | Are local guest seats without an account allowed? | Yes, with no saved progress. |
| Q7 | Where does the realm signing key live: a guest vTPM, the host-key credstore, or an offline operator hardware key with an online intermediate? | An online intermediate in the credstore, with the root on an operator FIDO2/hardware key. |
| Q8 | V1 exposure? | **Answered:** LAN and mesh only; public/WAN hosting is outside v1. |
| Q9 | One room per container (the literal direction), or shard containers when overhead shows in measurements? | One per container, with shard mode behind a measured switch. |
| Q10 | Can removing the vendor default password require `mios-bootstrap` to generate per-surface credentials, so no install is zero-config? | Yes, staged in P0b with a break-glass test. |

---

## Appendix A. Proposed SSOT additions (root applies them)

```toml
[accounts]                      # existing table; new keys
db_backed = true                # existing
name_regex = "^[a-z_][a-z0-9_-]{0,30}$"
reserved_names = ["root", "admin", "mios", "nobody", "mios-ai"]   # plus every sysusers name, checked by gate
invite_ttl_s = 86400
invite_max_uses = 1
ticket_ttl_s = 900
manifest_ttl_s = 86400
revocation_sla_s = 60
step_up_caps = ["mons.moderate", "vm.manage", "os.admin.linux", "os.admin.windows", "node.manage", "accounts.admin", "fleet.admin"]
capabilities = ["mons.play", "mons.host_room", "mons.moderate", "client.native", "client.web", "client.ssh", "client.windows",
                "remote.ssh", "remote.rdp", "remote.portal", "vm.console", "vm.manage", "os.login.linux", "os.admin.linux",
                "os.login.windows", "os.admin.windows", "node.login", "node.manage", "mesh.join", "accounts.admin", "fleet.admin"]
user_tier_ignored = true        # system consumers ignore ~/.config/mios for accounts.*

[accounts.ranges]               # uid tiers; realm-local
operator = [1000, 1999]
member   = [20000, 29999]
service_ns_windows_prefix = "mios-"

[accounts.roles.player]
grants = ["mons.play"]
groups = []
shells = []
memory_low_mib = 0

[accounts.roles.operator]
grants = ["mons.play", "mons.host_room", "os.login.linux", "remote.ssh", "remote.rdp", "remote.portal", "vm.console"]
groups = ["video", "render", "input"]   # never wheel/docker/libvirt/kvm by role; those are separate step-up grants
shells = ["/bin/bash", "/usr/bin/zsh"]
memory_low_mib = 10240

[accounts.federation]
enabled = false
# [[accounts.federation.trusted]] realm = "...", pubkey = "...", cap_ceiling = ["mons.play"]   (host tier only)

[mons]
remote_enabled = false
[mons.room]
rooms_per_container = 1
memory_mib = 32
pids = 16
cpus = 0.25
max_seats = 8
idle_reap_s = 300
pause = "owner_alone"
max_message_bytes = 16384
intent_rate_per_s = 10
chat_max_chars = 200
[mons.host]
max_rooms = 200
max_seats = 400
[mons.render]
idle_fps = 1
max_seats = 32
memory_mib = 64
[mons.gateway]
handshake_timeout_ms = 5000
max_conn_per_ip = 16
max_conn_per_account = 4
```

Ports: `[ports].mons_gateway` and `[ports].mons_ssh` in a new `[ports.categories.games]` band. Sysusers: `mios-accountdb`, `mios-identityd`, `mios-mons-gw`, `mios-mons-broker`, `mios-mons-rooms` (with linger and a subuid range), `mios-mons-render` and `mios-account-fetch`; groups `mios-accountdb-clients` and `mios-remote-ssh`.

## Appendix B. Hazards the design depends on (from the repo survey)

| ID | Hazard | Evidence | Owner / phase |
|---|---|---|---|
| H-1 | The agent-plane Postgres uses loopback `trust` and a superuser with a vendor password in a `Network=host` pod. RLS is opt-in and has `IS NULL` escapes. | `mios-pg-query:14`; `mios-pgvector.container:14-15`; `mios-ai.pod:21`; `schema-init.sql:624` | agent-plane lane. This design avoids depending on it (D2). |
| H-2 | Password hashes are in NOTIFY payloads, and an NT-hash column defaults to the empty password. | `schema-init.sql:1292-1336`, `:1230`; `mios-ai-firstboot:395` | P0a |
| H-3 | A root daemon uses f-string SQL, writes hashes back, and does not validate uid, gid, groups or shell. | `mios-account-sync:173`, `:180`, `:101-117`, `:242-247` | P0a, retired in P7 |
| H-4 | One vendor default password feeds every surface, with code fallbacks restating the literal. ttyd credentials are on argv. | `mios.toml:1760`; `mios_credentials.py:88-90`; `mios-ttyd-launch:79-81` | P0b |
| H-5 | The blanket 10 GiB `MemoryLow` applies to every user slice. | `mios.toml:834-836` | T20 (root) |
| H-6 | Task 153 (DB→SAM sync) is recorded completed, but nothing consumes `account_sync`. | `tasks.jsonl` #153; no consumer found by `git grep account_sync` outside schema/tests | root: reopen or annotate |
| H-7 | k3s would run N control planes, and Pacemaker is unfenced. | `mios.toml:3205-3212` | tasks 934/935 block P10 |
| H-8 | Headscale guest/workstation tags may reach `tag:blade:*`; the control plane is plain HTTP on `0.0.0.0`. | `headscale-policy.hujson:23-31`; `mios.toml:11332-11333` | P0a (tags), P8 (TLS) |
| H-9 | `pgvector` is in `[firewall].open_ports`. | `mios.toml:72` | P0a |
| H-10 | The secret-bearing `mios_security` tables are unconsumed, and the preauth key and keyfile are stored in plaintext. | `mios.toml` `[schema].unconsumed`; `schema-init.sql:1268`, `:1286` | P0a (drop or convert) |
| H-11 | `mios-mon.py` installs pip packages at runtime (Law 12), and its name collides with MiOS-Mons. | `usr/libexec/mios/mios-mon.py:27-40` | root (Q1) |
| H-12 | `[security].api_require_auth = false`; `principal_bind_mode`, `rule_of_two_mode` and `quarantine_mode` are all in audit mode. | `mios.toml:1805-1820` | P11 gate |

## Appendix C. Critique disposition

Key: **A** = accepted as written; **A\*** = accepted with a modification, stated; **R** = rejected, with the reason. "Where" names the section or task that answers the finding.

### C.1 `critique-security.md`

| Finding | Disposition | Where | Notes |
|---|---|---|---|
| C1 DB write becomes root on every node | **A** | D2, D4, §3.3-3.5, §3.8; T01-T03, T14, T20 | A separate identity cluster, no `trust`, definer-only roles, CHECKs mirrored in the agent, signed manifests only. |
| C2 NT hashes stored, broadcast, empty-password default | **A** | D3, §3.10; T01, T04, T24 | The NT-hash column is dropped. NOTIFY carries `{table, id, version}`. The default is deleted and the path fails closed. |
| C3 One vendor default secret on every surface | **A\*** | P0b; T06 | Accepted fully, but *staged*. Flipping the default to `""` alone would lock out installs, as the 0-byte-SSOT incident did, so `mios-bootstrap` per-surface generation and the break-glass test land in the same change. |
| C4 Hash write-back from any node | **A** | §3.8; T02, T20 | Nodes are read-only replicas. Divergence raises an alert and re-applies. |
| C5 SQL and argument injection | **A** | §3.4, §3.8; T02, T03 | One grammar enforced in three places, `--exec-json`, `--` before positionals, `openat`. |
| H1 Hashes world-readable, on argv, in the journal | **A** | §3.8; T02, T03 | 0600 before write, atomic rename, `chpasswd -e` on stdin until retirement, then no shadow edits at all. |
| H2 Revocation does not revoke | **A** | D11, §3.7, §4.6; T15, T19, T20 | Epoch, feed and SLA test. |
| H3 RLS opt-in and bypassed | **A\*** | §3.5 | Game state moves to its own database with definer functions and no `IS NULL` escapes. Fixing RLS on the agent-plane database is out of scope and recorded as H-1. |
| H4 DB and Blades reachable by players | **A** | §5.8; T05 | `pgvector` leaves `open_ports`. Mon tags are added. Players never get guest or workstation tags. Narrowing the *existing* guest→blade rule is root's call, flagged in T05. |
| H5 No tiers | **A\*** | D5, §3.2, §4.3 | The capability model is adopted. Revision3 permits standard OS identity provisioning under root-owned node policy. Clients cannot self-authorize elevated grants; administrator actions require independent approval and step-up. |
| H6 Identity defaults collide | **A** | §3.4, §4.5; T26 | Per-realm SID prefix, UUID identity, tiered ranges, per-account homes, idmap for federated accounts. |
| H7 Federation trust is unauthenticated | **A** | §4.5; T26 | Pinned anchors, `user@realm`, cap ceilings, enforce-mode gate. |
| H8 Room container inherits house defaults | **A\*** | D6, §5.3 | Accepted, except "pasta/slirp with a single published port", which is rejected in favour of UDS-only. It is stronger and avoids per-room ports. "Seccomp denies `execve` after start" is implemented as a filter the binary installs itself, because a Quadlet seccomp profile also applies to the entrypoint `execve`. |
| H9 Client auth is one password plus the operator shell | **A\*** | §5.4, §5.9, §6.9; T19, T22 | Accepted, except "SSH `ForceCommand` for a no-shell `mon-player` login", which is **rejected**. An OS login still creates a PAM session and a `user-<uid>.slice` carrying the blanket 10 GiB floor (H-5). SSH is terminated inside the gateway instead. The Origin check, rate limit, `Secure` cookie and per-user tokens are accepted (T22). |
| M1 Registration spam | **A** | §3.4, §4.3 | HMAC invites, quotas, lazy OS accounts. |
| M2 Room and gateway DoS | **A** | §5.4, §5.5, §7.2; T18 | Caps are SSOT keys. QUIC is not used in v1, so the reflection case does not arise. |
| M3 Terminal injection and spoofing | **A** | §6.11; T12, T18 | |
| M4 Cheating | **A** | §6.3, §6.8 | Intents only, server CSPRNG for duels, `PetView` without `rng`, flagged imports. |
| M5 Secrets in the DB, exempt from redaction | **A** | T04 | The unconsumed tables are dropped or converted to HMAC. Shrinking the redaction exemption is root's call. |
| M6 Windows agent is max-trust | **A\*** | §3.10; T24 | Namespace, signed manifests, code signing and serial logs are accepted. "A random per-account password delivered once to the user's device" is replaced by sealed delivery of the user-chosen OS password to the guest agent. A random password is unusable for console logon, and the plaintext never rests (Q2). Delivery uses virtio-serial, not WinRM. |
| M7 Agent lacks least privilege | **A\*** | §3.8; T20 | Hardening, fetch/apply split, allow-lists and break-glass are accepted. "Delegate homes to systemd-homed" is **rejected for v1**: homed conflicts with CephFS network homes (`[storage.cephfs]`) and the existing `/var/home` layout. The agent creates homes through `openat` plus tmpfiles semantics. |
| M8 SSOT is the wrong place for credentials | **A** | §4.2 | The SSOT holds policy, public keys and references. The user tier is ignored for `accounts.*`. |
| Design gates 1-10 | **A** | §2 (gate 1), §3.2 (2), §3.3 (3), D3/§3.9 (4), P0b (5), §5.3 (6), §3.7 (7), §4.5 (8), App. A (9), §3.8 (10) | |
| Required negative tests | **A** | TASKS-PROPOSAL acceptance criteria | Each one appears as a named negative criterion. |

### C.2 `critique-laws-ops.md`
The file was never written. The architect received only a truncated inline copy: findings C1 and C2 in full, and C3 cut off mid-sentence. The visible findings are dispositioned below. The rest of the laws and operations review was re-derived independently (LO-R1..R8) and covered by §7, §8 and Appendix B. **When the full critique turns up, any finding not listed here is open.**

| Finding | Disposition | Where | Notes |
|---|---|---|---|
| LO-C1 Hashes in the DB, broadcast, vendor DB password, shadow copied back | **A\*** | D2, D3; T01, T02, T04, T14 | Passwords never enter Postgres, and the expand/contract migration is accepted. "Registration goes to a root PAM/userdb broker on the node, following `mios-rdp-priv`" is modified. Registration is central (identityd), because accounts are global. The `mios-rdp-priv` pattern (socket-activated, peer-checked, uid 0 never admitted) is adopted for the node-side `apply` path and for `AuthorizedKeysCommand`. A dedicated `mons_broker` role limited to definer functions, and rooms without DB credentials, are accepted as written. |
| LO-C2 Root SQL injection from an app-registered name; tab-splitting breaks | **A** | §3.4; T02, T20 | An SSOT regex of at most 32 characters (matches `varchar(32)`), a DB CHECK, `--exec-json`, and a Rust port (the agent). |
| LO-C3 Players as OS users with default groups are root-equivalent and break the memory floors | **A** | D10, §3.11; T20 | Players are never OS users. Groups are allow-listed per role, and `wheel`, `docker`, `libvirt` and `kvm` are step-up grants, never role defaults. Floors are per role. The truncated remainder (`account-sync:114` falls back to `/bin/bash`) is covered by the shell allow-list. |
| LO-R1 (re-derived) The client redraws every iteration (`ui.rs:5101-5104`; `main` `:1876-1878`) | **A** | §7.1; T08 | Damage-driven redraw. B5. |
| LO-R2 (re-derived) One full game process per viewer through ttyd or tmux | **A** | D8, §6.9 | Thin clients and render seats. |
| LO-R3 (re-derived) Law 12: `mios-mon.py` pip-installs at runtime | **A** | H-11, Q1 | Outside this lane. Flagged to root. |
| LO-R4 (re-derived) Containers import `install.env` | **A** | §3.3, §5.3 | No new unit imports it. |
| LO-R5 (re-derived) Fleet hazards (k3s multi-server, unfenced Pacemaker) | **A** | §5.7; T25 deps | P10 is blocked on tasks 934 and 935. |
| LO-R6 (re-derived) Benchmarks could repeat the 15-lane OOM | **A** | §7.4 | Lane limit and `MemAvailable` check in the harness. |
| LO-R7 (re-derived) Law 4's scope for the room image | **A\*** | §5.2 | Treated as an app `Dockerfile`, following coderun. Root confirms. |
| LO-R8 (re-derived) ACCT-03 recorded complete with no runtime consumer | **A** | H-6 | Root reopens or annotates task 153. |

## Revision3 runtime triage clarification
Read-only inspection on mios found account-sync disabled/inactive and /run/userdb absent. Its tracked unit includes NoNewPrivileges and ProtectSystem=full; calling that unit universally unsandboxed is inaccurate. The SSOT unit block omits those restrictions, so generated and installed paths need separate audit. Code still accepts database-controlled OS parameters and adds wheel from is_admin. Contain this before enablement. This is a source-supported trust-boundary finding, not a demonstrated live fleet compromise. No account/service/database/firewall/credential changed.
