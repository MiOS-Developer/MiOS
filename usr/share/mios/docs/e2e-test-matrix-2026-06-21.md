<!-- AI-hint: End-to-end MiOS capability test matrix (2026-06-21). Records a 14-dimension, 54-agent adversarially-verified audit of the full verb/service/install surface (113 verb/feature rows: 63 PASS, 26 BUG, 11 GATED-OFF, 13 NOT-TESTABLE-HERE) and the 32 confirmed bugs with their fix + live-verification status. Companion to install-robustness-2026-06-21.md and aios-capability-gap-register-2026-06-21.md.
     AI-related: ./install-robustness-2026-06-21.md, ../mios.toml, ../../../lib/mios/agent-pipe/server.py -->
# MiOS end-to-end capability test matrix (2026-06-21)

A 14-dimension, 54-subagent **adversarially-verified** audit of the entire MiOS
surface (87 verbs across 16 groups + OpenAI conformance + OS/infra + install
reproducibility), each dimension doing static code review **and** read-only live
probing against the running dev VM (`podman-MiOS-DEV`), with every bug
independently re-checked before it counted. No live-launch / side-effecting verbs
were invoked (operator rule); those were verified by code path + broker/journal
evidence.

## Headline tally (113 verb/feature rows)

| Status | Count | Meaning |
|---|---:|---|
| **PASS** | 63 | Works / correctly wired, with live or code evidence |
| **BUG** | 26 | Broken or mis-wired (32 distinct confirmed defects across verbs+infra) |
| **GATED-OFF** | 11 | Intentionally inert (heavy lanes, federation surfaces, bare-metal-only) |
| **NOT-TESTABLE-HERE** | 13 | Needs an operator VM / bare metal / a live launch this session may not do |

## Per-dimension matrix

| Dimension | PASS | BUG | GATED-OFF | NOT-TESTABLE |
|---|---:|---:|---:|---:|
| oscontrol (window/launch) | 5 | 9 | – | – |
| pcinput_cu (input/vision) | 3 | 1 | 4 | 7 |
| memory | 4 | 1 | – | – |
| web (search/scrape/crawl) | 4 | – | – | – |
| search/find | 6 | 2 | – | – |
| system_services | 4 | 5 | – | – |
| packages | 8 | – | – | 6 |
| agent_a2a | 1 | – | 4 | – |
| code (coderun/sandbox) | 2 | – | 2 | – |
| text_docgen | 6 | – | – | – |
| recipes_misc | 5 | 1 | 1 | – |
| openai_conformance | 5 | 1 | – | – |
| infra_services | 7 | 1 | – | – |
| install_repro | 3 | 5 | – | – |

## Confirmed bugs + fix status (32)

Legend: **✅ FIXED+VERIFIED** (fix deployed + live-exercised this session) ·
**🟢 FIXED+DEPLOYED** (deployed clean, agent-pipe healthy, not individually
re-exercised) · **📝 FIXED (repo)** (install/PowerShell — can't live-test without
a fresh install; parse-clean) · **⚠️ FLAGGED** (complex / environmental /
build-or-operator-gated — see notes).

### HIGH

| # | Area | Bug | Status |
|---|---|---|---|
| 1 | oscontrol | `mios-window` had no executor health-gate → on WSL every window op routed to the dead `:11437` executor and `exit`ed on timeout, never falling through to the working local `mios-pc-control` path. Fixes **focus/move/position/resize/close/window_state** (6 verbs). | ✅ FIXED+VERIFIED (`mios-window list` rc=0 real data; `focus` returns graceful "no match" — was a timeout hang) |
| 2 | pcinput_cu | `cu_ground` vision fallback could never fire — `mios-pc-vision` not on PATH (missing tmpfiles shim). | ✅ FIXED+VERIFIED (`mios-pc-vision` now on PATH) |
| 3 | infra_services | `mios-llm-heavy-alt.container` had `Delegate=yes` in `[Container]` → Quadlet rejected the unit (`not-found`); the vLLM heavy-alt lane could never enable + `mios-llm-heavy`'s `Conflicts=` was dead. Moved to `[Service]`. | ✅ FIXED+VERIFIED (`mios-llm-heavy-alt.service` now generates) |
| 4 | recipes_misc | `os_recipe` HITL block-mode gated **every** recipe (incl. read-only show-network/disk-usage/service-status) because the gate keyed off the umbrella verb's `interactive` tier. Added `_effective_perm()` so the **named recipe's** tier governs. | ✅ FIXED+VERIFIED (`os_control_health` now executes; was `exit_code 126 hitl_blocked`) |
| 5 | system_services | `sys_env` / `sys_env_refresh` returned an empty `env` block — the env-probe's real cost (~24 s) exceeded a hard-coded 20 s timeout, swallowed silently. Raised to 60 s (env-tunable) + loud on timeout. | 🟢 FIXED+DEPLOYED |
| 6 | system_services | `container_restart` used `podman restart`, desyncing the managing systemd Quadlet. Made Quadlet-aware → delegates to `mios-restart` (systemctl). | 🟢 FIXED+DEPLOYED |
| 7 | web | `mios-firecrawl` (and agent-pipe `_firecrawl`) gated on pod-internal Redis `:6379` (unreachable from the host) → wrongly forced the healthy Firecrawl primary down to `web_extract_fallback`. Gate only on `:3002`. | 🟢 FIXED+DEPLOYED |
| 8 | search | `knowledge_search` broker (mios-ai) can't read OWUI `webui.db` (perms) → silently returns junk from the wrong store. | ✅ FIXED+VERIFIED (2026-06-22) — `mios-owui-bootstrap-admin` exports the admin api_key to `/etc/mios/owui-admin.key` (0640 root:mios-ai); `_admin_token` reads it; `_list_collections` lists via OWUI's API (0.9.6 `{items,total}`) so the broker no longer needs webui.db; a bare query now searches ALL collections. Live as mios-ai: key auths (200), lists `MiOS Documentation`, queries retrieval correctly. (0 hits only because that collection's files aren't vectorized in ChromaDB — an OWUI content state.) |
| 9 | packages/code | `coderun-sandbox` image never built (firstboot build hit "no egress") → `run_sandboxed_code` can't start its `@.container`. | ⚠️ FLAGGED — build/egress-gated; rebuild with egress (operator). |
| 10 | code | Host-side Code Mode socket proxy (`/run/coderun.sock`) has no host responder (per-session broker). | ⚠️ FLAGGED — multi-session broker design; not a quick fix. |
| 11 | system_services | `mios-sys-env-refresh.timer` ships but isn't enabled (no preset / firstboot wiring) → the sys_env cache goes stale between manual refreshes. | ⚠️ FLAGGED — wiring; on-demand `sys_env_refresh` works (now with the #5 timeout fix). |
| 12 | install_repro | `build-mios.ps1` hard-coded `M:\MiOS` for the log/data/config roots → logging breaks in non-admin (`%LOCALAPPDATA%`) installs. Now derived from the resolved `$script:MiosInstallDir`. | 📝 FIXED (repo) |

### MED

| # | Area | Bug | Status |
|---|---|---|---|
| 13 | system_services | `system_logs` had no `max_result_chars` → a default 50-line slice of a busy unit (~10 KB) truncated to ~1500 (≈19%). Added `max_result_chars = 12000`. | 🟢 FIXED+DEPLOYED |
| 14 | system_services | Restart verbs reported state immediately (fixed `sleep 2` + one `is-active`) → slow-start units mis-reported. Added a bounded readiness poll (30 s, terminal-state break, elapsed). | 🟢 FIXED+DEPLOYED |
| 15 | search | `everything_search` (`mios-everything`) `set -e`/pipefail aborted the script on a nonzero `es.exe` BEFORE the unreachable-vs-no-match JSON envelope could emit. Added `|| true`. | 🟢 FIXED+DEPLOYED |
| 16 | search | `knowledge_search` pgvector fallback queried the chat-memory `knowledge` table and returned ~0.51 off-topic rows as answers. Added an independent relevance floor (`MIOS_KNOWLEDGE_FALLBACK_FLOOR`=0.62). | 🟢 FIXED+DEPLOYED |
| 17 | install_repro | Version-pinning half-implemented (mechanism present; `mios.toml [bootstrap].mios_ref/bootstrap_ref` missing; bootstrap re-clone didn't read the ref). Added the keys + env-override read. | 📝 FIXED (repo) |
| 18 | install_repro | The in-WSL bootstrap re-clone (`$seedScript`) had no fetch retry. Wrapped in a 3× backoff loop, degrade-not-abort. | 📝 FIXED (repo) |

### LOW

| # | Area | Bug | Status |
|---|---|---|---|
| 19 | memory | `memory_update` reported `updated:1 / ok:true` even when the key matched zero rows. Added an existence check → `matched:false / no_such_key` on a miss. | ✅ FIXED+VERIFIED |
| 20 | web | Firecrawl async-job poll used the crawl-status endpoint (latent). Switched to `/v1/scrape/{job_id}` + data-present completion. | 🟢 FIXED+DEPLOYED |
| 21 | search | `plocate`/`locate` absent → fs_search always paid the ~11 s find-walk. Added `plocate` to `[packages.utils]`. | 📝 FIXED (repo; lands on rebuild) |
| 22 | openai_conformance | Primary `tool_choice='required'` reached the llama.cpp `llm_light` lane un-downgraded (200-accepts-but-ignores) → silently non-forcing on the primary path. Gated the primary through `_endpoint_supports_tool_choice` like the council/secondary path. | 🟢 FIXED+DEPLOYED |
| 23 | install_repro | Generated `mios build` driver staging lacks the curl-fallback the menu path has (hardcoded `/mnt/m`, WARN-only on miss). | ⚠️ FLAGGED — LOW; fragile PS here-string, not worth an untestable edit. |
| 24 | recipes_misc | `mios-cron-director` daemon inactive+disabled despite preset `enabled` → scheduled jobs don't fire. | ⚠️ FLAGGED — wiring. |
| 25 | oscontrol | `move_window`/`position_window` named regions (left/right/corners) have no LOCAL actuator on WSL once the executor is absent (only center/numeric/focus/close fall back). | ⚠️ FLAGGED — secondary; needs region→rect geometry in the local fallback. |
| 26 | search | `everything_search` `es.exe` (v1.1.0.30) is version-mismatched to the running Everything → IPC exit 8. | ⚠️ FLAGGED — environmental (operator upgrades Everything); the #15 fix now returns a clean `everything_unreachable` envelope instead of a bare exit. |

> The remaining confirmed-bug rows are duplicates of the above across the verb/infra
> projections (e.g. each of the 6 window verbs and both firecrawl gate sites count
> separately in the 32 total but share one root-cause fix).

## OWUI login (operator-reported, fixed this session)

- **`mios-hermes-firstboot` was failing** — `mios-owui-bootstrap-admin` did
  `INSERT INTO user (… api_key …)` but OWUI 0.9.6 dropped the `api_key` column →
  no admin user existed at all. Fixed with a **schema-adaptive INSERT** (introspect
  live columns; only `id` is NOT NULL).
- **Password is now SSOT-sourced** — resolves `MIOS_OPERATOR_PASSWORD` →
  `MIOS_OWUI_ADMIN_PASSWORD` → `mios.toml [identity].default_password` →
  `MIOS_IDENTITY_DEFAULT_PASSWORD` → `"mios"` (the same chain Forge/Portal/Cockpit/RDP use)
  and **reconciles on every boot** (forge's model), never a random password.
- **Proven**: signin `admin@mios.local` / `mios` → valid admin token (on the host `open_webui` port).
- **Latent note**: the OWUI quadlet `PublishPort=…:8080` vs uvicorn `--port 3030`
  is inconsistent (currently harmless — `podman port` empty, reached via the bridge);
  flagged for careful cleanup.

## NOT-TESTABLE-HERE (operator-VM / bare-metal / live-launch gated)

- `pc_*` / `cu_*` click/type/key/screenshot — depend on the Windows `:11437`
  executor and/or would put input/windows on the operator's screen (forbidden).
- `bootc switch` / `upgrade` / `rollback` — need a real bootc target (qcow2/VM),
  not the WSL overlay dev VM.
- VFIO GPU passthrough, k3s + Ceph one-node cluster — bare-metal only.
- Artifact cuts (`just raw|iso|qcow2|vhdx|wsl2`, `verify-images`, `sbom`) + a green
  CI image build — operator-run.

## What was VERIFIED PASS live (highlights)

AI plane up end-to-end (agent-pipe on the `agent_pipe` port 200, the `llm_light` GPU lane 200, `:8765`
MCP 200, hermes on the `hermes` port auth-gated); RTX 4090 live; webtools pod running
(crawl4ai `:11235`, firecrawl `:3002`, redis, worker); `system_status`,
`os_control_health`, memory round-trip, and the full read-only system/search/
package surface all returned real data.

## Operational verification — MiOS AI fully operational (2026-06-22)

Live chat through the canonical `agent_pipe` front door (full refine→council→polish
pipeline on the RTX 4090):

| Test | Result | Evidence |
|---|---|---|
| **A — generation** | ✅ PASS (16 s) | "capital of Japan" → `Tokyo` |
| **B — web research + citations** | ✅ PASS (25 s) | real kernel answer with **5 `url_citation` annotations** + source URLs (kernel.org / wikipedia) — no fabrication |
| **C — forced tool-failure honesty** | ✅ PASS (16 s) | read of a nonexistent file → "the live tool output indicates this file does not exist… no text to report" (no fabricated contents) |
| **D — env grounding via OWUI user header** | ✅ PASS (15 s) | `x-openwebui-user-email` header → `webui.db` lookup → grounded to `America/New_York` (the user's stored timezone) |

**OWUI Environment-Details Integration (operator's Antigravity plan) — completed + verified.**
Part 2 (agent-pipe `_client_env`: read `x-openwebui-user-{email,id}` headers +
`webui.db` `timezone`/`info`/`settings` → blend into the **system-role** env block,
not the user message) was already implemented; Part 1 (`mios-open-webui.container`:
`ENABLE_API_KEY`→`ENABLE_API_KEYS` for OWUI 0.9.6 + `ENABLE_FORWARD_USER_INFO_HEADERS`)
deployed + committed. Test D confirms the chain end-to-end.

**System health (2026-06-22):** **0 failed units**; the `agent_pipe`/`hermes`/`llm_light`/`:8765`/`open_webui` ports
all healthy; `mios-sys-env-refresh.timer` + `mios-cron-director.service` now
**enabled + active** (preset fix); OWUI login works with the SSOT password;
`mios-llm-heavy-alt.service` generates (gated-off). The deploy-time lesson: repo
Quadlets carry `${VAR:-default}` render placeholders — they MUST be rendered
(34-render-quadlets.sh, or `sed`) before deploying to a live VM, else podman
fails to parse them (observed: an OWUI restart broke on the raw `PublishPort`
placeholder; recovered by rendering).
