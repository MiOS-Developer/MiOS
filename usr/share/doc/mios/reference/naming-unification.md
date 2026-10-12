<!-- AI-hint: The WS-NAME workflow -- a TRUE GLOBAL minification of MiOS naming: collapse the entire TOML-key/env-var/verb/const surface onto ONE deterministic, capability-matched unified names+keys registry (mios.toml section.key <-> MIOS_SECTION_KEY, 1:1), deleting the hand-maintained translation tables. Convention + registry + phased global migration + drift-gate. -->
<!-- AI-related: tools/lib/userenv.sh, usr/lib/mios/userenv.sh, automation/lib/globals.sh, automation/lib/globals.ps1, usr/share/mios/mios.toml, usr/lib/mios/mios_toml.py, automation/98-drift-checks.sh -->

# WS-NAME — Global Naming Minification → one unified names/keys registry

**Status:** migration under verification · **Source:** operator directives 2026-07-10 and 2026-10-08 · **Effort:** XL (global, phased)

Current implementation uses `tools/native/mios-resolver/src/names.rs` for the
canonical transform and naming metadata. `mios-gen names --root .` exposes this
metadata without configuration values; `mios-gen names-registry` projects the
tracked names artifacts. Generated globals use the same export implementation.
Canonical nonempty inputs win; unique legacy inputs remain supported, with
conflicts rejected before applying any override. Ambiguous aliases and aliases
with different semantics are reported separately and never merged by spelling.

`mios-gen sync --root . --plan` validates the SSOT-declared generation pipeline
before writes. `mios-gen sync --root .` executes all existing stages. The shell
entry point and standalone names generator now delegate to the native command
and library. Compatibility deletion, remaining script ports, installed platform
verification and live configurator synchronization are still open; the phases
below describe the complete target rather than a completion claim.

## Goal

A **true global minification and refactoring** of every name in MiOS — TOML keys,
env vars, verbs, shell/PS constants, configurator data-keys, emitted vars — into
**ONE unified names+keys registry** where every name:

1. **matches its capability** (the name says what it is/does — semantic, not arbitrary),
2. follows **one naming convention** (deterministic, no exceptions), and
3. is **minified**: exactly one canonical name per capability — no synonyms, no
   duplicates, no hand-maintained translation layer, and
4. is **folded**: *similar* capabilities are combined into one unified entry
   (often parametric) rather than N near-duplicate names — e.g. the many
   per-model/per-endpoint/per-tokenizer vars fold into one indexed/typed family;
   the per-color vars are one `colors.<name>` family; per-service ports are one
   `ports.<service>` family. Fold by capability, not by lexical accident.

**Hard invariant: NO loss of functionality.** Folding combines *names*, never
behaviors — every capability reachable today stays reachable (same values, same
effect); a fold that would drop or merge a real behavior is not done. This is a
rename/collapse only.

## The model: ONE registry, no translations

There is **one unified names/keys registry** — the single SSOT for naming. It holds
**one entry per capability** (minimal, combined: duplicates/synonyms merged). Every
surface **sources the same canonical identifier directly from that one entry** —
nothing is mapped, aliased, or translated:

- **No translation layer.** The 418-entry `userenv.sh` key→env table, the
  121-const `globals.sh`/`.ps1` mirror, and every emitter rename are **deleted**.
  There is no per-name authored mapping anywhere — a name exists in exactly one
  place (the registry) and is emitted verbatim to each surface's lexical form.
- **One identifier per capability.** Each registry entry carries the capability's
  canonical token (matching what it *does*). Its TOML key and its `MIOS_*` env name
  are the **same identifier** in the two required lexical forms (dotted-lower for
  TOML, `MIOS_`-UPPER_SNAKE for env) — generated together from the one entry, so
  they can never diverge. No reader-flavored prefixes (`MIOS_VERB_*`,
  `MIOS_AGENT_PIPE_*`), no arbitrary shortenings (`MIOS_USER`, `MIOS_KEYBOARD`), no
  suffix forms (`*_PORT`).
- **Verbs + consts** are entries in the same registry: a verb's name and its backend
  (`mios-<capability>`) come from its entry; a const is just an entry's env form —
  no independently-authored constant for a value the registry already owns.

### Why this is THE minification

Today ~1,290 names are **individually authored** across `userenv.sh` (418),
`globals.sh`/`.ps1` (121×2), emitters, verbs, and configurator — many are synonyms
for the same capability. Collapsing to **one registry, sourced everywhere** removes
every authored mapping and every synonym: the name count drops to the number of
distinct capabilities, and each is defined once.

## Registry artifact

The registry is an authored SSOT — `usr/share/mios/names.toml` (or a `[names]` tree
in `mios.toml`): one entry per capability = `{ token, kind, description }`. From it,
generators emit every surface: the env exports (replacing `userenv.sh`), the
`globals.*` consts, the verb→backend bindings, the configurator `data-key`s. A
committed `names.generated.txt` mirror is the auditable flat list; the drift-gate
regenerates and diffs it. This file/tree **is** the unified names/keys list.

## Global fix categories (what the migration collapses)

From the 2026-07-10 ultracode inventory (418 userenv + 121 globals + 635 misc +
117 verbs):

- **Reader-flavored prefixes** → capability prefix: `MIOS_VERB_EMBED_MODEL`,
  `MIOS_AGENT_PIPE_VISION_MODEL`, `MIOS_STACK_MODEL`, `MIOS_MICRO_*`,
  `MIOS_TOKENIZER_*` → `MIOS_AI_*` (their key lives under `[ai]`).
- **Short/native aliases** → deterministic key form: `MIOS_USER` →
  `MIOS_IDENTITY_USERNAME`, `MIOS_HOSTNAME` → `MIOS_IDENTITY_HOSTNAME`,
  `MIOS_KEYBOARD` → `MIOS_LOCALE_KEYBOARD_LAYOUT`, `MIOS_TIMEZONE` →
  `MIOS_LOCALE_TIMEZONE`, `MIOS_AI_KEY` → `MIOS_AI_API_KEY`, `MIOS_COLOR_*` →
  `MIOS_COLORS_*`, etc. (These were the "translations"; now 1:1 with the key.)
- **Suffix-form ports** → prefix form: `MIOS_K3S_API_PORT` → `MIOS_PORTS_K3S_API`
  (section is `[ports]` → `MIOS_PORTS_*`, uniformly).
- **Emitter/synonym collapse**: any var emitted under a second name
  (`MIOS_AI_BACKEND`, `MIOS_AI_HEAVY_ENDPOINT`, …) → drop the synonym; keep the one
  key-derived name.
- **De-dup**: two keys/consts for one capability → one canonical key; delete the rest.

> Decision to confirm during Phase 0: `[ports]` → `MIOS_PORTS_*` (pure determinism)
> vs the entrenched `MIOS_PORT_*` (12/16 already). Pick ONE and apply globally; the
> drift-gate then enforces it. (Deterministic-from-section favors `MIOS_PORTS_`.)

## Phased global migration (surface-shrinking)

- **Phase 0 — freeze the convention + generator.** Land the `section.key ->
  MIOS_SECTION_KEY` transform in `mios_toml.py` + a generator that emits
  `naming.generated.txt`. Resolve the `[ports]` prefix decision. No renames yet.
- **Phase 1 — resolver becomes deterministic.** Replace the 418-entry `userenv.sh`
  table with the generic transform (both parity copies; drift-check 27 `check_bootstrap_sync` / `check_userenv_parity`). Emit BOTH
  the new canonical name AND the legacy name (compat shim) for one release.
- **Phase 2 — repoint every reader**, domain by domain, lowest-blast-radius first
  (AI-plane ~3-4 readers each; ports 5-16; identity/colors moderate). Update
  `globals.*`, emitters, configurator data-keys, code, units, docs to the canonical.
- **Phase 3 — delete the shims + legacy names.** Remove every legacy alias so only
  the unified registry ships. `grep` of any legacy name = 0.

Each phase: `just drift-gate` green + `test_mios_*` pass + byte-parity held.

## Drift-gate (make it self-enforcing)

New `automation/98-drift-checks.sh` check: regenerate `naming.generated.txt` and
FAIL if (a) it differs from the committed copy, (b) any exported `MIOS_*` var is not
a deterministic image of a `mios.toml` key, or (c) a second name maps to a key that
already has a canonical env name. This makes the minimal unified surface permanent.

## Blast radius & risks

- **Scope:** global — ~1,290 authored names collapse to the SSOT key tree (one name
  per capability). Highest-reader migrations: `MIOS_PORT_PGVECTOR` (16),
  `MIOS_PORT_HERMES` (14), `MIOS_PORT_AGENT_PIPE`/`LLM_LIGHT` (12),
  `MIOS_IDENTITY_USERNAME` (27), `MIOS_DESKTOP_FLATPAKS` (10), `MIOS_IMAGE_BASE` (10).
- **Risks:** `userenv.sh` byte-parity; `install.env` emitters read on both Windows
  and Linux; configurator data-keys (598, already key-shaped — low risk); the
  compat-shim phase guarantees no functional gap. Do it domain-by-domain, verify each.

## Phase 1 — Kept Legacy Compatibility Mappings (Shims)

During Phase 1, the following legacy non-canonical environment variable mappings are preserved as compat shims in the `userenv.sh` translation slots to guarantee backward compatibility:

- **Identity & Accounts:**
  - `identity.username` maps to `MIOS_USER` and `MIOS_DEFAULT_USER` (canonical: `MIOS_IDENTITY_USERNAME`)
  - `identity.fullname` maps to `MIOS_USER_FULLNAME` (canonical: `MIOS_IDENTITY_FULLNAME`)
  - `identity.hostname` maps to `MIOS_HOSTNAME` and `MIOS_DEFAULT_HOST` (canonical: `MIOS_IDENTITY_HOSTNAME`)
  - `identity.shell` maps to `MIOS_USER_SHELL` and `MIOS_DEFAULT_SHELL` (canonical: `MIOS_IDENTITY_SHELL`)
  - `identity.groups` maps to `MIOS_USER_GROUPS` and `MIOS_DEFAULT_GROUPS` (canonical: `MIOS_IDENTITY_GROUPS`)
  - `identity.default_password` maps to `MIOS_DEFAULT_PASSWORD` (canonical: `MIOS_IDENTITY_DEFAULT_PASSWORD`)
  - `accounts.db_backed` maps to `MIOS_ACCOUNTS_DB_BACKED` (canonical: `MIOS_ACCOUNTS_DB_BACKED`)

- **Locale:**
  - `locale.timezone` maps to `MIOS_TIMEZONE` and `MIOS_DEFAULT_TIMEZONE` (canonical: `MIOS_LOCALE_TIMEZONE`)
  - `locale.keyboard_layout` maps to `MIOS_KEYBOARD` and `MIOS_DEFAULT_KEYBOARD` (canonical: `MIOS_LOCALE_KEYBOARD_LAYOUT`)
  - `locale.language` maps to `MIOS_LOCALE` and `MIOS_DEFAULT_LOCALE` (canonical: `MIOS_LOCALE_LANGUAGE`)

- **Auth:**
  - `auth.ssh_key_action` maps to `MIOS_SSH_KEY_ACTION` (canonical: `MIOS_AUTH_SSH_KEY_ACTION`)
  - `auth.password_policy` maps to `MIOS_PASSWORD_POLICY` (canonical: `MIOS_AUTH_PASSWORD_POLICY`)

- **Network:**
  - `network.firewalld_default_zone` maps to `MIOS_FIREWALLD_ZONE` (canonical: `MIOS_NETWORK_FIREWALLD_DEFAULT_ZONE`)

- **AI Platform:**
  - `ai.endpoint` maps to `MIOS_AI_ENDPOINT` (canonical: `MIOS_AI_ENDPOINT`)
  - `ai.model` maps to `MIOS_AI_MODEL` (canonical: `MIOS_AI_MODEL`)
  - `ai.api_key` maps to `MIOS_AI_KEY` (canonical: `MIOS_AI_API_KEY`)
  - `ai.key` maps to `MIOS_AI_KEY` (canonical: `MIOS_AI_KEY`)
  - `ai.embed_model` maps to `MIOS_VERB_EMBED_MODEL` and `MIOS_AI_EMBED_MODEL` (canonical: `MIOS_AI_EMBED_MODEL`)
  - `ai.stack_model` maps to `MIOS_STACK_MODEL` (canonical: `MIOS_AI_STACK_MODEL`)
  - `ai.chat_vision_model` maps to `MIOS_AGENT_PIPE_VISION_MODEL` (canonical: `MIOS_AI_CHAT_VISION_MODEL`)
  - `ai.system_prompt_file` maps to `MIOS_SYSTEM_PROMPT_FILE` (canonical: `MIOS_AI_SYSTEM_PROMPT_FILE`)
  - `ai.tokenizer_backend` maps to `MIOS_TOKENIZER_BACKEND` (canonical: `MIOS_AI_TOKENIZER_BACKEND`)
  - `ai.tokenizer_encoding` maps to `MIOS_TOKENIZER_ENCODING` (canonical: `MIOS_AI_TOKENIZER_ENCODING`)
  - `ai.tokenizer_cache_dir` maps to `MIOS_TOKENIZER_CACHE_DIR` (canonical: `MIOS_AI_TOKENIZER_CACHE_DIR`)
  - `ai.tokenizer_path` maps to `MIOS_TOKENIZER_PATH` (canonical: `MIOS_AI_TOKENIZER_PATH`)
  - `ai.hermes_agent_repo` maps to `MIOS_HERMES_AGENT_REPO` (canonical: `MIOS_AI_HERMES_AGENT_REPO`)
  - `ai.hermes_agent_ref` maps to `MIOS_HERMES_AGENT_REF` (canonical: `MIOS_AI_HERMES_AGENT_REF`)
  - `ai.hermes_backend_url` maps to `MIOS_HERMES_BACKEND_URL` (canonical: `MIOS_AI_HERMES_BACKEND_URL`)
  - `ai.mcp_registry` maps to `MIOS_MCP_REGISTRY` (canonical: `MIOS_AI_MCP_REGISTRY`)
  - `ai.agent_venv` maps to `MIOS_HERMES_VENV` (canonical: `MIOS_AI_AGENT_VENV`)
  - `ai.agent_install_dir` maps to `MIOS_HERMES_DIR` (canonical: `MIOS_AI_AGENT_INSTALL_DIR`)

- **Ports:**
  - `ports.ssh` maps to `MIOS_PORT_SSH` and `MIOS_SSH_PORT` (canonical: `MIOS_PORTS_SSH`)
  - `ports.forge_http` maps to `MIOS_PORT_FORGE_HTTP` and `MIOS_FORGE_HTTP_PORT` (canonical: `MIOS_PORTS_FORGE_HTTP`)
  - `ports.forge_ssh` maps to `MIOS_PORT_FORGE_SSH` and `MIOS_FORGE_SSH_PORT` (canonical: `MIOS_PORTS_FORGE_SSH`)
  - `ports.cockpit` maps to `MIOS_PORT_COCKPIT` and `MIOS_COCKPIT_PORT` (canonical: `MIOS_PORTS_COCKPIT`)
  - `ports.searxng` maps to `MIOS_PORT_SEARXNG` and `MIOS_SEARXNG_PORT` (canonical: `MIOS_PORTS_SEARXNG`)
  - `ports.hermes` maps to `MIOS_PORT_HERMES` and `MIOS_HERMES_PORT` (canonical: `MIOS_PORTS_HERMES`)
  - `ports.adguard_dns` maps to `MIOS_PORT_ADGUARD_DNS` (canonical: `MIOS_PORTS_ADGUARD_DNS`)
  - `ports.adguard_ui` maps to `MIOS_PORT_ADGUARD_UI` (canonical: `MIOS_PORTS_ADGUARD_UI`)

- **Image & Sidecars:**
  - `image.base` maps to `MIOS_BASE_IMAGE` (canonical: `MIOS_IMAGE_BASE`)
  - `image.bib` maps to `MIOS_BIB_IMAGE` (canonical: `MIOS_IMAGE_BIB`)
  - `image.branch` maps to `MIOS_BRANCH` (canonical: `MIOS_IMAGE_BRANCH`)
  - `image.local_tag` maps to `MIOS_LOCAL_TAG` (canonical: `MIOS_IMAGE_LOCAL_TAG`)
  - `image.sidecars.*` map to `MIOS_*` without `IMAGE_SIDECARS` prefix (e.g., `image.sidecars.k3s` maps to `MIOS_K3S_IMAGE`)

- **Services:**
  - `services.forge.user/uid/gid` map to `MIOS_FORGE_USER/UID/GID` (canonical: `MIOS_SERVICES_FORGE_USER/UID/GID`)
  - `services.searxng.user/uid/gid` map to `MIOS_SEARXNG_USER/UID/GID` (canonical: `MIOS_SERVICES_SEARXNG_USER/UID/GID`)
  - `services.ceph.user/uid/gid` map to `MIOS_CEPH_USER/UID/GID` (canonical: `MIOS_SERVICES_CEPH_USER/UID/GID`)
  - `services.hermes.user/uid/gid` map to `MIOS_HERMES_USER/UID/GID` (canonical: `MIOS_SERVICES_HERMES_USER/UID/GID`)
  - `services.open_webui.user/uid/gid` map to `MIOS_OPEN_WEBUI_USER/UID/GID` (canonical: `MIOS_SERVICES_OPEN_WEBUI_USER/UID/GID`)
  - `services.pgvector.user/uid/gid` map to `MIOS_PGVECTOR_USER/UID/GID` (canonical: `MIOS_SERVICES_PGVECTOR_USER/UID/GID`)
  - `services.llamacpp.user/uid/gid` map to `MIOS_LLAMACPP_USER/UID/GID` (canonical: `MIOS_SERVICES_LLAMACPP_USER/UID/GID`)
  - `services.agent_pipe.user/uid/gid` map to `MIOS_AGENT_PIPE_USER/UID/GID` (canonical: `MIOS_SERVICES_AGENT_PIPE_USER/UID/GID`)
  - `services.webtools.user/uid/gid` map to `MIOS_WEBTOOLS_USER/UID/GID` and legacy `MIOS_CRAWL4AI_USER/UID/GID`
  - `services.adguard.user/uid/gid` map to `MIOS_ADGUARD_USER/UID/GID` (canonical: `MIOS_SERVICES_ADGUARD_USER/UID/GID`)

- **Storage & CephFS:**
  - `storage.cephfs.xdg_cache_home_override` maps to `MIOS_XDG_CACHE_LOCAL_PATH` (canonical: `MIOS_STORAGE_CEPHFS_XDG_CACHE_HOME_OVERRIDE`)
  - `storage.cephfs.*` map to `MIOS_CEPHFS_*` prefix instead of `MIOS_STORAGE_CEPHFS_*`

- **WSL2:**
  - `wsl2.desktop_compat.gdk_backend` maps to `MIOS_WSLG_GDK_BACKEND` (canonical: `MIOS_WSL2_DESKTOP_COMPAT_GDK_BACKEND`)
  - `wsl2.desktop_compat.moz_wayland` maps to `MIOS_WSLG_MOZ_WAYLAND`
  - `wsl2.desktop_compat.qt_platform` maps to `MIOS_WSLG_QT_PLATFORM`
  - `wsl2.dev_vm.quadlet_network_mode` maps to `MIOS_QUADLET_DEV_NETWORK_MODE`
