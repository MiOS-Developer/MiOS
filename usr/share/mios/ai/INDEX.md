<!-- AI-hint: The primary architectural specification and API-surface map for MiOS as a whole system: how an immutable bootc/OCI Fedora workstation is also a local, self-replicating agentic AI OS. Defines the OpenAI-compatible routing (agent-pipe, port key agent_pipe; MiOS-Hermes, port key hermes; prefilter, port key prefilter), the function-named inference lanes (mios-llm-light, port key llm_light, primary + embeddings; mios-llm-heavy vLLM, port key vllm; mios-llm-heavy-alt SGLang, port key sglang), the PostgreSQL+pgvector agent datastore (port key pgvector), the eight Architectural Laws, and the layered mios.toml SSOT.
     AI-related: /usr/share/mios/llamacpp/llama-swap.yaml, /usr/share/mios/postgres/schema-init.sql, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /etc/mios/profile.toml, /usr/share/mios/profile.toml, /usr/share/mios/env.defaults, /etc/mios/env.d/, /etc/mios/install.env, /etc/mios/ai, /etc/mios/forge -->
# 'MiOS' System Interface -- v0.3.0

Single source of truth for 'MiOS' architectural laws and the OpenAI-compatible
API surface. Sourced from `Containerfile`, `automation/`, `usr/lib/bootc/`,
`usr/share/mios/ai/v1/`, the live Quadlets under
`usr/share/containers/systemd/`, and the upstream specs cited inline.

## 0. What MiOS is, end to end

'MiOS' is one system with two faces that are the same thing:

1. **An immutable, bootc-managed Fedora workstation OS** shipped as an OCI image
   (`ghcr.io/mios-dev/mios:latest`). The repo root *is* the system root --
   `usr/`, `etc/`, `srv/`, `var/` mirror where files land on a booted host. The
   image is built by the `Containerfile` + numbered `automation/[0-9][0-9]-*.sh`
   sub-phases, verified by `bootc container lint` (Architectural Law 4), and
   delivered/updated as an atomic image via the bootc lifecycle.
2. **A local, self-replicating agentic AI OS** layered on top of that image: a
   fleet of local LLM inference lanes, an agent orchestration pipeline, a
   unified agent-memory datastore, and an MCP/A2A capability surface -- all
   fully offline, no vendor cloud, no hardcoded vendor URLs.

This document maps the **AI face** and the contracts that bind it to the OS
face. The flow it describes, top to bottom:

```
build pipeline (Containerfile + automation/) -> OCI image -> bootc lifecycle
        │
        ▼  (the booted host runs:)
 inference lanes (mios-llm-light / -heavy / -heavy-alt)
        │  OpenAI /v1 + embeddings
        ▼
 agent orchestration (agent-pipe -> MiOS-Hermes + council peers)
        │  refine -> swarm/DAG -> tool-loop -> polish
        ▼
 agent memory (PostgreSQL + pgvector :5432) · capability surface (MCP / A2A)
```

Everything below is the contract for one of those stages and how it serves the
whole.

## 1. System profile

'MiOS' is an immutable, bootc-managed Linux workstation OS distributed as an
OCI image. Source: `README.md`, `Containerfile`, `CLAUDE.md`. Image:
`ghcr.io/mios-dev/mios:latest`. Every shipped artifact follows the
`mios-<component>` lowercase-kebab naming convention (the former *CloudWS*
project name is retired -- e.g. `mios-guacamole`, `mios-pxe-hub`,
`mios-guacd`, `mios-guacamole-postgres`, `mios-crowdsec-dashboard`).

## 2. API surface (OpenAI-compatible)

The AI face presents **one canonical OpenAI-compatible surface** so any OpenAI
SDK / OWUI / LAN client talks to MiOS without vendor-specific glue (Architectural
Law 5). The layers, front to back:

- **MiOS-Agent pipe** at `$MIOS_AI_ENDPOINT` (port key `agent_pipe`;
  `mios-agent-pipe.service`)
  -- the full pipeline front door. It refines the prompt, decomposes into a
  swarm/DAG of concurrent sub-agents, runs the standard tool-loop, streams
  reasoning emits, and polishes the final answer. This is what Open WebUI and
  the desktop client target.
- **MiOS-Hermes** gateway on the `hermes` port (the
  `hermes-agent.service` host-direct install) -- the OpenAI-compat agent gateway
  the pipe orchestrates: sessions, tool-calling, skills, kanban.
- **MiOS-Prefilter** on the `prefilter` port -- a thin HTTP forwarder that
  injects `tool_choice=delegate_task` on fan-outable prompts then passes through
  to MiOS-Hermes. It is a *subset* of the agent-pipe (no refine/swarm/emits) and
  remains available as an override drop-in.
- **Inference lanes** one layer down (function-named, not upstream-tool-named):
  raw model inference + embeddings live on **mios-llm-light** (port key
  `llm_light`); two gated heavy GPU lanes (**mios-llm-heavy** vLLM, port key
  `vllm`; **mios-llm-heavy-alt** SGLang, port key `sglang`) co-serve the heavy
  reasoner when VRAM permits. See §2a.

The endpoints below follow the OpenAI public API spec
(<https://platform.openai.com/docs/api-reference>) verb-for-verb;
`x-mios.*` rows are MiOS extensions, namespaced so strict OpenAI
clients can ignore them.

| Path | Method | Served by | Spec |
|---|---|---|---|
| `/v1/chat/completions` | POST | MiOS-Agent pipe (port key `agent_pipe`) -- refines, decomposes, tool-loops, polishes | <https://platform.openai.com/docs/api-reference/chat> |
| `/v1/responses` | POST | MiOS-Agent pipe (port key `agent_pipe`) -- backed by Hermes (port key `hermes`) by default | <https://platform.openai.com/docs/api-reference/responses> |
| `/v1/embeddings` | POST | MiOS-Agent pipe (port key `agent_pipe`); proxied to **mios-llm-light** (port key `llm_light`, `nomic-embed-text`) | <https://platform.openai.com/docs/api-reference/embeddings> |
| `/v1/models` | GET | MiOS-Agent pipe; manifest at `usr/share/mios/ai/v1/models.json` | <https://platform.openai.com/docs/api-reference/models/list> |
| `/v1/agents` (manifest) | GET | `usr/share/mios/ai/v1/agents.json` -- mirror of `[agents.*]` in mios.toml | -- |
| `x-mios:/v1/mcp` | GET | `usr/share/mios/ai/v1/mcp.json` | <https://modelcontextprotocol.io/specification> |

`/v1/mcp` is a MiOS extension (not part of the OpenAI public API). The
canonical OpenAI route to invoke an MCP server is
`POST /v1/responses` with `tools=[{"type": "mcp", "server_url": ...}]`;
the manifest at `/v1/mcp` is what MiOS agents read to populate that
`tools` array. The `x-mios:` prefix is a documentation marker only --
the served URL is `/v1/mcp`. The MCP surface is how MiOS agents discover
and call **tools**; the A2A surface (agent cards at `/v1/agents`,
`a2a-peers.json`) is how they discover and delegate to **agents** -- the
two together are how the agentic AI OS extends and federates without
hardcoded plumbing.

LiteLLM and any other OpenAI-shaped backend are supported as drop-in
alternates but are NOT started by default -- flip them on in `[ai]` of
`mios.toml`. Default deployment is **mios-llm-light inference + MiOS-Agent-pipe
front door + MiOS-Hermes gateway**, with the heavy lanes gated off.

### 2a. Inference lanes + default model set

Inference lanes are named for their **function**, not for the upstream engine
they happen to embed. The engines speak the OpenAI- / Ollama-compatible API,
which is why OWUI's `RAG_*` knobs and every OpenAI SDK work unchanged.

| Lane (unit) | `[ports]` key | Engine (upstream image) | Role | State |
|---|---|---|---|---|
| **mios-llm-light** (`mios-llm-light.service`) | `llm_light` | llama.cpp via the **llama-swap** proxy (`ghcr.io/mostlygeek/llama-swap:cuda`) | PRIMARY everyday inference + embeddings + the `mios-opencode` coder model; multi-model auto-swap with per-conversation KV-paging to disk | default-on (gated on baked GGUFs) |
| **mios-llm-heavy** (`mios-llm-heavy.service`) | `vllm` | vLLM (`vllm/vllm-openai`), PagedAttention + APC | heavy GPU reasoner, served-name `mios-heavy` | gated/off (VRAM) |
| **mios-llm-heavy-alt** (`mios-llm-heavy-alt.service`) | `sglang` | SGLang (`lmsysorg/sglang`) | alternate heavy reasoner, served-name `mios-heavy` | gated/off (VRAM) |
| **mios-llm-worker@** (template) | -- | single-model swarm worker | one model per process for fan-out concurrency | on-demand |

`mios-llm-light` is the linchpin of the everyday lane: llama-swap launches/swaps
a `llama-server` per requested model behind ONE OpenAI `/v1` endpoint, and each
`llama-server` checkpoints/restores that conversation's KV to disk
(`--slot-save-path` + the agent-pipe's `_kv_paging` over `POST /slots/{id}`) --
the fleet-wide AIOS Context Manager. The embed model runs an `--embedding`
`llama-server` so `/v1/embeddings` is served locally on the same port. Config:
`usr/share/mios/llamacpp/llama-swap.yaml`. The two heavy lanes are mutually
exclusive at runtime (both advertise the served-model-name `mios-heavy`); enable
one on a GPU with headroom, after baking weights offline.

The `[ai]` section of `usr/share/mios/mios.toml` is the SSOT for which models the
pipeline targets and which one MiOS-Hermes uses by default. The set is sized for
the canonical 32 GB+ system-RAM, GPU-accelerated workstation (CPU fallback
otherwise).

| Slot | Default | Notes |
|---|---|---|
| big chat / code (`[ai].big_ram_model`) | `mistral-magistral-small-2509` | 16 GB+ dGPU class; clean JSON tool-call output; promoted by the host auto-pick when VRAM allows |
| base chat (`[ai].model`) | `granite4.1:8b` | ~3.4 GB resident reasoning base; the fallback on hosts without an auto-pick |
| CPU children / swarm fan-out | `lfm2:700m` | sub-200 ms spawn; ~4 GB resident; good for grep/inspect/report subtasks |
| coding specialist (MiOS-OpenCoder) | `mios-opencode` | first-class OpenAI `/v1` council peer (`mios-opencode-gateway.service`, port key `opencode_gateway`), dispatched by the orchestrator; served by mios-llm-light |
| embeddings (`[ai].embed_model`) | `nomic-embed-text` | 768-dim, 8192-token context; OpenAI `/v1/embeddings` shape, served by mios-llm-light |

The runtime "which model does Hermes use by default" knob is `[ai].big_ram_model`;
the inference backend Hermes forwards to is `[ai].hermes_backend_url`
(`http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1` -- the mios-llm-light lane). GGUFs are an opt-in
offline build bake; a missing-weights lane stays inert (its
`ConditionPathExists=` model-ready guard short-circuits the unit so it can't
crash-loop). The host overlay at `/etc/mios/mios.toml` takes precedence over the
vendor default at `/usr/share/mios/mios.toml`; the bootstrap installer runs
hardware detection then offers a profile picker whose selection is written back
into `/etc/mios/mios.toml`. Restart the relevant lane unit and
`hermes-agent.service` after editing.

> Historical note: earlier MiOS releases used **Ollama** (the since-retired
> Ollama lane) for inference and embeddings. Ollama has been fully removed as a MiOS backend
> (containers, firstboot, model-bake, Modelfiles, CLI shim); inference and
> embeddings now run on `mios-llm-light` (port key `llm_light`). "Ollama" survives only as an
> *upstream API-compat reference* -- the lanes speak the OpenAI/Ollama-compatible
> API -- and in migration notes.

### 2b. Agent memory + datastore

The unified agent-plane datastore is **PostgreSQL + pgvector** -- the
`mios-pgvector` container (`mios-pgvector.service`, host-net, port key `pgvector`, uid 826),
one engine for relational + JSONB (document) + vector (pgvector HNSW) memory.
Schema is initialised idempotently from
`usr/share/mios/postgres/schema-init.sql` (tables: `agent_memory`, `event`,
`tool_call`, `session`, `skill`, `scratch`, `knowledge`, `sys_env`, `kanban`,
`directory_entry`, `person`, `agent_keypair`, …). Clients use the pure-python
`mios-pg-query` (loopback trust) and `mios-db --pg`. This is what makes the agent
OS *learn*: every finished Q+A, tool call, and derived fact is persisted and
recalled by cosine similarity (embeddings via mios-llm-light's `nomic-embed-text`)
and injected back into agent context.

> Historical note: the agent store was previously **the legacy datastore** (BSL 1.1) with
> **Qdrant** as a vestigial vector store. Both are fully removed; pgvector is the
> single FOSS (PostgreSQL License + pgvector) datastore for relational, document,
> and vector memory.

## 3. Architectural laws (enforced; non-negotiable)

These eight laws are the contract every other part of the system is built to
satisfy -- they keep the OS immutable, auditable, and self-replicating.

| # | Law | Enforced by |
|---|---|---|
| 1 | **USR-OVER-ETC** -- static config in `/usr/lib/<component>.d/`; `/etc/` is admin-override only. Exceptions documented per-file (e.g., `/etc/yum.repos.d/`, `/etc/nvidia-container-toolkit/` -- upstream-contract surfaces). | `automation/`, `usr/lib/`, `etc/` |
| 2 | **NO-MKDIR-IN-VAR** -- every `/var/` path declared via `usr/lib/tmpfiles.d/*.conf`; never written at build time. | `usr/lib/tmpfiles.d/mios*.conf` |
| 3 | **BOUND-IMAGES** -- every Quadlet image symlinked into `/usr/lib/bootc/bound-images.d/` and baked into `/usr/lib/containers/storage` at build time. Binder loop: `automation/01-system-files-overlay.sh:74-86`. | `usr/lib/bootc/bound-images.d/` |
| 4 | **BOOTC-CONTAINER-LINT** -- final RUN of `Containerfile`. Fail = fail the build. | `Containerfile` (last `RUN`) |
| 5 | **UNIFIED-AI-REDIRECTS** -- every OpenAI-API-shaped client resolves through one canonical surface: `MIOS_AI_ENDPOINT` (the OpenAI-SDK `base_url` slot), `MIOS_AI_MODEL` (default model id), `MIOS_AI_KEY` (api key, empty for the local proxy). No vendor-hardcoded URLs. | `/etc/profile.d/mios-env.sh`, `usr/bin/mios`, `usr/bin/mios-env`, `etc/mios/ai/` |
| 6 | **UNPRIVILEGED-QUADLETS** -- every Quadlet declares `User=`, `Group=`, `Delegate=yes`. Documented exceptions: `mios-ceph` and `mios-k3s` declare `User=root`/`Group=root` because Ceph/K3s require uid 0; `mios-forgejo-runner` declares `User=0`/`Group=0` because the closed self-replication loop runs `podman build -f /Containerfile` on every push (needs write access to rootful `/var/lib/containers/storage/` and `bootc switch` permissions on the resulting image); the upstream `mios-llm-heavy` (SGLang) image runs image-default root because it probes GPU memory via a root-only `nvidia-smi` and has no `mios` user. Rationale lives in each unit's file header. | `etc/containers/systemd/`, `usr/share/containers/systemd/` |
| 7 | **NO-HARDCODE** -- nothing operator-tunable, including model names, ports, or scoring parameters, may be hardcoded. Values must resolve via the `mios.toml` configuration cascade. | `usr/share/mios/mios.toml`, `C:\mios-bootstrap\mios.toml` |
| 8 | **K-I-S-S-LANGUAGES** -- all scripting and automation must consolidate strictly around modern, FOSS, user-selected, and defined languages (Nushell, Rust, Zig) using Keep-It-Simple-Stupid (K.I.S.S.) principles to eliminate shell script sprawl. | `C:\MiOS\automation\17-accounts-db.sh`, `language_modernization_blueprint.md` |

## 4. Profile + environment resolution

Both the user profile (TOML) and runtime environment (env-style) follow a
three-layer overlay. Higher layers supersede lower layers field-by-field.

**Profile layers** (read by `mios-bootstrap/install.sh:load_profile_defaults`
and at runtime by `mios` CLI clients):

1. `~/.config/mios/profile.toml` -- per-user override (highest precedence;
   seeded into every uid≥1000 home from `/etc/skel/.config/mios/profile.toml`)
2. `/etc/mios/profile.toml` -- host/admin override (shipped by `mios-bootstrap`)
3. `/usr/share/mios/profile.toml` -- vendor defaults (shipped by `mios.git`,
   immutable, USR-OVER-ETC)

**Environment layers** (resolved by `/etc/profile.d/mios-env.sh` at
login; later sources override earlier values, so this list runs from
**lowest** precedence to **highest**):

1. `/usr/share/mios/env.defaults` -- vendor defaults (lowest)
2. `~/.env.mios` -- legacy per-user (deprecated; honored only when no
   admin/host/current source supplies the same key)
3. `/etc/mios/env.d/*.env` -- admin/distro drop-ins (alphabetical)
4. `/etc/mios/install.env` -- host identity, written by bootstrap
5. `~/.config/mios/env` -- canonical per-user override (highest)

The CLI `/usr/bin/mios-env` prints the resolved surface (`--explain`
shows which layer supplied each key).

**Build-time variables** read by `Justfile`:

| Variable | Scope | Purpose |
|---|---|---|
| `MIOS_AI_KEY` / `MIOS_AI_MODEL` / `MIOS_AI_ENDPOINT` | AI | Resolution per LAW 5; defaults in `usr/share/mios/env.defaults`. |
| `MIOS_BASE_IMAGE` | build | OCI base image (default `ghcr.io/ublue-os/ucore-hci:stable-nvidia`, `Justfile:45`). |
| `MIOS_LOCAL_TAG` | build | Local image tag (default `localhost/mios:latest`, `Justfile:13`). |
| `MIOS_IDENTITY_USERNAME` / `MIOS_IDENTITY_HOSTNAME` | build | Default account/hostname baked into the image (`Containerfile:26-27`). |
| `MIOS_DESKTOP_FLATPAKS` | build | Comma-separated Flatpak refs (`Containerfile:28`). |

## 5. Defaults policy

Every boolean feature flag in `usr/share/mios/profile.toml` and
`/etc/mios/profile.toml` ships **`true`**. The system never disables a
component via static config -- when a component is incompatible with the
host (wrong virtualization layer, missing required path, missing
hardware), systemd `Condition*` directives in the corresponding
Quadlet/service unit short-circuit it at boot/pre-boot and the unit
silently no-ops. Any flag can be set to `false` in the layered
`mios.toml` to force-disable a component even when its conditions
would otherwise allow it.

Active gating (referenced in `etc/containers/systemd/` and
`usr/share/containers/systemd/`):

| Unit | Condition | Skips on |
|---|---|---|
| `mios-ai` | `ConditionPathIsDirectory=/etc/mios/ai` | bootstrap incomplete |
| `mios-llm-light` | `ConditionPathExists=/usr/share/mios/llamacpp/models/.ready` | GGUFs not yet baked/provisioned |
| `mios-llm-heavy` (vLLM) | `ConditionPathExists=/var/lib/mios/vllm/model/config.json` | heavy weights not baked (VRAM-gated; off by default) |
| `mios-llm-heavy-alt` (SGLang) | `ConditionPathExists=/var/lib/mios/vllm/model/config.json` | heavy weights not baked (VRAM-gated; off by default) |
| `mios-pgvector` | `ConditionVirtualization=\|!container`, `\|wsl` | true nested container (overlay-on-overlay PGDATA); runs on bare-metal + WSL2 |
| `mios-ceph` | `ConditionPathExists=/etc/ceph/ceph.conf`, `!container` | Ceph not configured, nested |
| `mios-k3s` | `!wsl`, `!container` | WSL2, nested containers |
| `mios-crowdsec-dashboard` | `ConditionPathExists=/etc/crowdsec/config.yaml` | CrowdSec not configured |
| `mios-guacamole`, `mios-guacd`, `mios-guacamole-postgres` | `!container` | nested containers |
| `mios-pxe-hub` | `!wsl`, `!container` | virtualized hosts without routable LAN |
| `mios-gpu-{nvidia,amd,intel,status}` | `ConditionPathExists=/dev/...`, `!container`, `!wsl` (Intel) | no matching GPU device |
| `mios-forge` | `ConditionPathIsDirectory=/etc/mios/forge`, `!container` | bootstrap incomplete, nested |
| `mios-forge-firstboot` | `ConditionPathExists=/etc/mios/install.env`, `!sentinel`, `!container` | install.env absent, already ran, nested |
| `mios-cockpit-link` | `ConditionPathExists=/usr/lib/systemd/system/cockpit.socket`, `!container` | Podman Desktop UI shim that publishes `:19090` → host `:9090` so the Cockpit web console is clickable from the container view; skipped when cockpit isn't installed |

## 6. User-definitions consolidation (single source of truth)

The user-definitions surface is **two files**:

| Role | Path (canonical FHS) | Format | Owned by | Edited via |
|---|---|---|---|---|
| **Source of truth** | `~/.config/mios/mios.toml` (per-user, highest precedence) | TOML 1.0.0 | host owner (per-account) | configurator UI / text editor |
|  | `/etc/mios/mios.toml` (per-host) | TOML 1.0.0 | host admin | configurator UI / text editor |
|  | `/usr/share/mios/mios.toml` (vendor) | TOML 1.0.0 | image | shipped read-only |
| **Derived bridge** | `/etc/mios/install.env` | env-var | system | regenerated by `mios-sync-env` |

`mios.toml` is the human-edited surface. `install.env` is the
shell/systemd-side derivation (`EnvironmentFile=`, `source` in
firstboot scripts). The bridge is `mios-sync-env` -- run after editing
`mios.toml` to refresh `install.env`. Secrets that never round-trip
through `mios.toml` (`MIOS_USER_PASSWORD_HASH`,
`MIOS_FORGE_ADMIN_PASSWORD`, `MIOS_GITHUB_TOKEN`) are preserved
verbatim from the previous `install.env`.

**Deprecated** (read for backward compatibility, never written; will
be removed in a future schema-major bump):

| Path | Replaced by |
|---|---|
| `~/.env.mios` | `[env]` block in `~/.config/mios/mios.toml` |
| `~/.config/mios/env` | `[env]` block in `~/.config/mios/mios.toml` |
| `~/.config/mios/profile.toml` | `[profile]` section in `mios.toml` |
| `/etc/mios/profile.toml` | `[profile]` section in `mios.toml` |
| `/usr/share/mios/profile.toml` | merged into `mios.toml` |

CLI:

```
mios-env             # print resolved MIOS_* surface
mios-env --explain   # show which layer supplied each key
mios-env --json      # machine-readable
mios-sync-env        # regenerate /etc/mios/install.env from mios.toml
mios-sync-env --dry-run        # preview
mios-sync-env --show-source    # print layered TOML before output
```

### 6a. Configurator UI (unified-dotfile editor)

The unified user-definitions dotfile (`/etc/mios/mios.toml` host;
`~/.config/mios/mios.toml` per-user) is TOML 1.0.0
(<https://toml.io/en/v1.0.0>). The on-disk format is FHS-compliant
(`/etc/mios/` for host config, `/usr/share/mios/` for vendor defaults)
and round-trippable; every MiOS resolver -- `build-mios.sh`'s
`toml_get_layered`, `build-mios.ps1`'s `Resolve-MiosTomlAiDefaults`,
`tools/lib/userenv.sh`'s Python merger -- consumes the same schema.

Schema versioning lives in the `[meta]` section
(`schema_version` + `mios_version` + `format` + `spec_url`) so
parsers can refuse mismatched versions cleanly.

A static, dependency-free editor ships at:

```
/usr/share/mios/configurator/index.html
```

The page is opened locally (`file://` or behind any HTTP server --
Cockpit can serve it; no extension required). **Open** loads the
current `mios.toml`; identity / locale / AI / network / Quadlet-
enable fields are edited via the form; **Save** downloads the
updated TOML. No data leaves the browser; no install step. The page
targets the same `schema_version` that the build-mios prompts and
runtime resolvers consume, so edits flow through end-to-end.

## 7. Service access surface (LAN-reachable by default)

Every 'MiOS' service binds `0.0.0.0` on its listening port so the same
deployment is reachable from
- `127.0.0.1` / `::1` (local loopback)
- the host LAN IP (remote-LAN access on bare-metal, Hyper-V VM, or
  WSL2 with `networkingMode=mirrored`)
- Podman bridge / `cni0` / `virbr0` (sibling-container access on
  `mios.network` and the libvirt bridge)

Container Quadlets use `PublishPort=0.0.0.0:HOST:CONTAINER` (Podman's
default already binds 0.0.0.0; the explicit prefix makes the contract
auditable). The host-net inference + datastore lanes (`mios-llm-light`,
`mios-llm-heavy`, `mios-pgvector`) bind their port directly on the host
(`Network=host`) for reliable `localhost:<port>` from the pipe + swarm nodes.
Apps inside bridge-net containers that take an explicit listen address get
`Environment=ADDRESS=...` / `FORGEJO__server__HTTP_ADDR=0.0.0.0` as appropriate.
Cockpit on the host listens via `cockpit.socket` whose default
`ListenStream=9090` already binds the wildcard address.

Firewalld is the actual gate. Default zone is `drop`; the ports below
are opened by `automation/25-firewall-ports.sh` (build-time) and
`automation/33-firewall.sh` (runtime mios-firewall-init). Port values are
SSOT in `[ports]` of `mios.toml`:

| Port  | Proto | Service | Notes |
|---|---|---|---|
| 53    | udp | adguard_dns          | AdGuard Home DNS resolver (tailnet-wide global DNS) |
| 8033  | tcp | **MiOS-OWUI**       | Open WebUI (browser front; OWUI Quadlet → :8080 in container) |
| 8053  | tcp | mios-adguard        | AdGuard Home web UI + REST API |
| 8080  | tcp | mios-guacamole      | Browser desktop web UI |
| 8090  | tcp | cockpit             | host web console |
| 8091  | tcp | mios-cockpit-link   | Podman Desktop discovery shim |
| 8119  | tcp | hermes dashboard    | Hermes Dashboard web UI (sessions, skills, stats) |
| 8222  | tcp | sshd                | host admin sshd (hardened off :22) |
| 8300  | tcp | mios-forge          | Forgejo HTTP web UI |
| 8301  | tcp | mios-forge          | Forgejo git+ssh (vacated 2222 for host admin sshd) |
| 8389  | tcp | RDP                 | GNOME Remote Desktop / xRDP |
| `pgvector` | tcp | **mios-pgvector**   | PostgreSQL + pgvector unified agent DB |
| `vllm` | tcp | **mios-llm-heavy** | vLLM heavy dGPU lane (gated/off by default) |
| `sglang` | tcp | **mios-llm-heavy-alt** | SGLang heavy dGPU lane (gated/off by default) |
| 8443  | tcp | mios-k3s            | Kubernetes API |
| 8444  | tcp | mios-ceph           | Ceph dashboard |
| `llm_light` | tcp | **mios-llm-light**  | PRIMARY inference + embeddings (llama.cpp via llama-swap; backs Hermes) |
| 8458  | tcp | **mios-cpu-node**   | Always-on CPU granite-brain lane |
| `opencode_gateway` | tcp | **MiOS-OpenCoder**  | opencode → OpenAI `/v1` gateway shim (loopback only) |
| `agent_pipe` | tcp | **MiOS-Agent pipe** | Full agentic pipeline front door (refine → swarm/DAG → tool-loop → polish); what OWUI + desktop target |
| `prefilter` | tcp | **MiOS-Prefilter**  | Delegation prefilter; injects `tool_choice=delegate_task` then forwards to MiOS-Hermes |
| `hermes` | tcp | **MiOS-Hermes**     | OpenAI-compat agent gateway (host-direct, NOT a Quadlet) |
| 8800  | tcp | mios-code-server    | code-server (VS Code in a browser) |
| `searxng` | tcp | **MiOS-Search**     | SearXNG; backs `web_search` tool + OWUI's web augmentation |
| 8681  | tcp | ttyd_bash           | ttyd browser pty -- Linux bash session |
| 8682  | tcp | ttyd_powershell     | ttyd browser pty -- Windows PowerShell session |

Internal-only / loopback services (no LAN `PublishPort`): `mios-pgvector`
(port key `pgvector`, host-net loopback agent DB), `mios-guacd` (4822),
`mios-webtools-firecrawl-api` (8302), `mios-webtools-crawl4ai` (8235),
`prefilter` (port key `prefilter`), `arbiter` (8650), `daemon_agent` (8644), `model_router` (8645),
`oscontrol` (8453), `mcp` (8460). Only `Network=host` is used for primary AI-plane
Quadlets, so ports are bound directly on the host interface.

## 8. Global pipeline phases

The end-to-end bootstrap → install pipeline -- how the image is built and a host
is brought up -- is partitioned into five phases shared across both repos:

| Phase | Owner repo | Purpose |
|---|---|---|
| Phase-0 | `mios-bootstrap` | Preflight, profile load, identity capture |
| Phase-1 | `mios-bootstrap` | Total Root Merge (clone `mios.git` into `/`, overlay bootstrap) |
| Phase-2 | `mios` | Build (Containerfile + `automation/[0-9][0-9]-*.sh` sub-phases, OR dnf install on FHS) |
| Phase-3 | both | sysusers/tmpfiles/services + user create + per-user `~/.config/mios/{profile.toml,system-prompt.md}` staging |
| Phase-4 | `mios-bootstrap` | Reboot |

The user profile card at `etc/mios/profile.toml` (host) and
`~/.config/mios/profile.toml` (per-user) is read in Phase-0 to seed defaults
and re-written/staged in Phase-3. The closed self-replication loop
(`mios-forgejo-runner`) re-runs this build on every push to the in-distro Forge,
so the system can rebuild and `bootc switch` itself -- the "self-replicating"
half of the AI OS.

## 9. Cross-references

- Build pipeline architecture: `CLAUDE.md`, `automation/build.sh`.
- Filesystem and hardware layout: `usr/share/doc/mios/concepts/architecture.md`.
- Inference-lane conversion (Ollama → llama.cpp): `usr/share/mios/llamacpp/llama-swap.yaml`, `usr/share/doc/mios/concepts/`.
- Agent datastore (PostgreSQL + pgvector): `usr/share/mios/postgres/schema-init.sql`.
- Security posture and hardening kargs: `SECURITY.md`, `usr/lib/bootc/kargs.d/`.
- Build modes (CI, Linux, Windows, self-build): `usr/share/doc/mios/guides/self-build.md`.
- Contribution conventions: `CONTRIBUTING.md`.
- Component licenses: `usr/share/doc/mios/reference/licenses.md`.
