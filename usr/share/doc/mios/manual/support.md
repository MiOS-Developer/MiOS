<!-- AI-hint: Manual pages distilled from the source comments of support, sanitized, each passage anchored to the comment it came from. -->

# support

### AI-facing doc genericity audit. Walks every doc that gets...

AI-facing doc genericity audit.

Walks every doc that gets loaded into an LLM's context window and
flags content bound to a single deployment / operator / project
state. Covers:

  * /usr/share/mios/hermes/skills/*/SKILL.md   (per-skill guidance)
  * /usr/share/mios/ai/*.md                    (system + SOUL docs)

Findings:
  * conversational tone in body prose ("operator-flagged YYYY-MM-DD")
  * hardcoded paths bound to a single user (/mnt/c/Users/<name>,
    /var/home/<name>); operator name is an SSOT variable
    ([identity].username -> MIOS_IDENTITY_USERNAME)
  * hardcoded hostnames (MiOS-955, mios-ec377, ...)
  * project-internal phase jargon in YAML frontmatter description
    (descriptions get surfaced to LLM context; jargon noise wastes
    tokens + leaks implementation detail)

These are LLM-guidance docs, so prose in the body is EXPECTED.
The check is for guidance bound to a specific operator / machine
/ project state vs. guidance portable to any MiOS deployment.

Exits 0 (clean) / 1 (findings).

<!-- mios-src:65a0b7c0f706 from automation/support/audit-hermes-skills.py:5-29 -->

### Idempotent patch

Idempotent patch: give Hermes' BACKGROUND REVIEW the full global tool
surface ("make sure MiOS-Hermes can use all global
tools!! ... and all Global MiOS tools for Hermes too").

Upstream `agent/background_review.py` runs the post-turn self-improvement
pass under a thread-local tool whitelist built from ONLY the ["memory",
"skills"] toolsets -- everything else is denied at runtime. That made the
review agent's `patch` call fail ("Background review denied non-whitelisted
tool: patch. Only memory/skill tools are allowed."), so when its skill_manage
edit missed it had no working file-edit fallback, looped on a malformed
recreate, and burned the tool-turn budget ("agent may appear stuck").

This patch UNIONS the parent agent's full tool surface (`agent.valid_tool_names`
-- the same global tools the main loop has, MiOS verbs included) into the
review whitelist, so the background pass is no longer denied any tool. It also
softens the now-false "other tools will be denied" instruction. Memory/skill
tools remain first-class via the existing prompt; this only REMOVES the cap.

Idempotent: re-runs are no-ops once the marker is present (survives image
rebuilds; re-applied by automation/72-hermes-agent.sh over each site-packages).
Run: python3 hermes-background-review-tools-patch.py <path/to/background_review.py>

<!-- mios-src:195f18162af7 from automation/support/hermes-background-review-tools-patch.py:4-25 -->

### In-place patch of hermes_cli/web_server.py so the /api/pty...

In-place patch of hermes_cli/web_server.py so the /api/pty endpoint
honors HERMES_PTY_SHELL env var.

Upstream hermes-agent hardcodes `_resolve_chat_argv` to spawn
`hermes --tui` (the Node-built TUI chat). MiOS-DEV wants a plain bash
shell in the dashboard's /chat tab (operator directive
"do we have a react window for terminal(s)?" -> chose "plain bash").
Setting `HERMES_PTY_SHELL=/bin/bash` (or any shell binary) replaces
the hardcoded TUI spawn with the requested shell.

Idempotent: rerunning is a no-op once the marker comment is present.
Safe: leaves the upstream fallback when HERMES_PTY_SHELL is unset.

Usage:
    hermes-dashboard-shell-patch.py /path/to/hermes_cli/web_server.py

<!-- mios-src:89f7c843cfc6 from automation/support/hermes-dashboard-shell-patch.py:4-19 -->

### Strip externally-hosted asset URLs from the built Hermes...

Strip externally-hosted asset URLs from the built Hermes dashboard.

Runs after `npm run build` against `<repo>/hermes_cli/web_dist`. The
upstream React bundle ships five OPTIONAL theme stylesheets that
reference `fonts.googleapis.com` for typography (Inter, JetBrains Mono,
Spectral, IBM Plex, Share Tech Mono, Fraunces, DM Mono). The DEFAULT
theme uses the @nous-research/ui bundled woff2 fonts (in `web/public/
fonts/`) and works offline. Patching the optional-theme URLs to an
inert `data:text/css,` URI keeps the theme switcher's UI alive but
turns the non-default themes into a no-op rather than a Google Fonts
fetch.

Architectural Law 7 (OFFLINE-FIRST): the runtime must never reach out
to an external service. Build-time deps (npm install from registry)
happen once during image build; runtime is offline.

Usage:
    hermes-dashboard-strip-externals.py /path/to/hermes_cli/web_dist

<!-- mios-src:f28ea281a9e5 from automation/support/hermes-dashboard-strip-externals.py:4-22 -->

### In-place patch of gateway/platforms/discord.py to add...

In-place patch of gateway/platforms/discord.py to add progressive
"thinking" reactions on the operator's Discord message during agent
processing.

Operator directive "also add more reactions to the
MiOS-Hermes Discord bot--Should be using more discord reactions to
show it's thinking!"

Upstream hermes-agent's Discord gateway emits exactly two reactions:
  on_processing_start    -> 👀 (single "looking" emoji)
  on_processing_complete -> ✅ / ❌

That gives the operator no visibility into what stage the agent is in
mid-run. This patch enriches the reaction surface with a progressive
sequence:
    📡 (received)          immediate
    🧠 (thinking)          after 2s if still processing
    🛠️ (using tools)       after 8s if still processing
    ⏳ (still working)      after 20s if still processing
    ✅ / ❌ (final)         on completion (and all phase reactions
                            are cleared first so the final outcome
                            stands alone)

A background asyncio.create_task() drives the progression so the
gateway's normal flow isn't blocked. The task is stashed on the
gateway instance keyed by Discord message id so concurrent
in-flight messages each get their own task that the matching
on_processing_complete can cancel.

Idempotent: rerunning is a no-op once the marker comment is present.
Safe: if Discord's add_reaction / remove_reaction fail (rate limit,
missing perm), each call already swallows the exception in the
existing _add_reaction / _remove_reaction helpers, so the progression
degrades silently.

Usage:
    hermes-discord-reactions-patch.py /path/to/discord.py

<!-- mios-src:72d662d789d6 from automation/support/hermes-discord-reactions-patch.py:4-41 -->

### Locate the contiguous on_processing_start +...

Locate the contiguous on_processing_start + on_processing_complete
    method pair by line-scanning. Returns (start_idx, end_idx) as half-
    open slice indices into `lines`. (-1, -1) if not found.

    Line-by-line scanning avoids the catastrophic backtracking that a
    nested-quantifier regex hits on this 5500-line file (the upstream
    discord.py has dozens of `async def` at 4-space indent, and the
    regex explores every alignment).

<!-- mios-src:e413ead8f7a4 from automation/support/hermes-discord-reactions-patch.py:105-113 -->

### !/usr/bin/env python3 AI-hint: Audit script to identify and...

!/usr/bin/env python3
AI-hint: Audit script to identify and flag non-portable, environment-specific data (hardcoded paths, hostnames, or project-specific jargon) in LLM-facing documentation within the hermes/skills and ai directories.
AI-related: /usr/share/mios/hermes/skills/, /usr/share/mios/ai/, /usr/share/mios/hermes/skills, /usr/share/mios/ai, mios-ec377
AI-functions: split_frontmatter, audit_one, audit_ai_doc, main

<!-- mios-src:161b30da3816 from automation/support/audit-hermes-skills.py:1-4 -->

### !/bin/bash AI-hint: Use this script to provision the...

!/bin/bash
AI-hint: Use this script to provision the mios-pgvector PostgreSQL container, including setting up the data directory, deploying the schema, rendering the Quadlet configuration, and verifying the vector extension.
AI-related: /usr/share/mios/postgres, /usr/share/mios/postgres/schema-init.sql, mios-pgvector, mios-pgvector.container, mios-pgvector.service

<!-- mios-src:716ea37dcfdf from automation/support/bringup-pgvector.sh:1-3 -->

### !/bin/bash AI-hint: Executes targeted Day-0 cleanup of...

!/bin/bash
AI-hint: Executes targeted Day-0 cleanup of PostgreSQL/pgvector tables, daemon states, skills catalogs, agent passports, and audit logs to purge persistent state when standard cache clearing is insufficient.
AI-related: mios-cache-clear, mios-daemon, mios-skills-miner, mios-passport-provision, mios-skills-miner.timer, mios-passport-provision.service
AI-functions: pgvector, daemon, skills, passports, agentpipe, audit, ttyd, pycache

<!-- mios-src:dcbf79267239 from automation/support/day0-extras.sh:1-4 -->

### !/bin/bash AI-hint: Restarts core MiOS agent and daemon...

!/bin/bash
AI-hint: Restarts core MiOS agent and daemon services to clear stale state and regenerate day-0 credentials/keys after a system wipe or configuration reset.
AI-related: mios-agent-pipe, mios-daemon, mios-open-webui, mios-agent-pipe.service, mios-daemon.service

<!-- mios-src:69ef8dad8df0 from automation/support/day0-restart.sh:1-3 -->

### !/bin/bash AI-hint: Automates the deployment of the...

!/bin/bash
AI-hint: Automates the deployment of the agent-pipe service by copying source files, stripping CRLF, performing a pre-restart import check in the service venv, and rolling back to backups if the import fails.
AI-related: /usr/lib/mios/agent-pipe, /usr/lib/mios/agents/.venv/bin/python3, /usr/lib/mios/agent-pipe/, /usr/share/mios/mios.toml, /usr/share/mios/mios.toml.bak-, mios-agent-pipe, mios-agent-pipe.service

<!-- mios-src:4261f116cf36 from automation/support/deploy-agent-pipe.sh:1-3 -->

### !/bin/bash AI-hint: Automates the deployment of firstboot...

!/bin/bash
AI-hint: Automates the deployment of firstboot binaries and systemd drop-in configurations, then triggers and validates the mios-hermes-firstboot service to ensure environment variables and core services are initialized.
AI-related: /usr/libexec/mios/mios-hermes-firstboot, mios-hermes-firstboot, mios-paths-env, hermes-agent.service, mios-hermes-firstboot.service

<!-- mios-src:5d5f073823c7 from automation/support/deploy-firstboot-fixes.sh:1-3 -->

### !/bin/bash AI-hint: Hot-deploys source-only MiOS binaries...

!/bin/bash
AI-hint: Hot-deploys source-only MiOS binaries, configuration files (tmpfiles/sysusers), and OWUI tools to the live VM's /usr path without a full image rebuild to apply immediate updates to the broker and system services.
AI-related: /usr/share/mios/openwebui/tools, /usr/share/mios/openwebui/tools/mios_computer_use.py, mios-coderun-sandbox, mios-launcher-daemon, mios-db, mios-docgen, mios-coderun-codemode, mios-stresstest, mios-owui-install-computer-use, mios-hermes-firstboot

<!-- mios-src:43c741136d3d from automation/support/deploy-tooling-live.sh:1-3 -->

### !/bin/bash AI-hint: Removes pre-LLM RAG knowledge...

!/bin/bash
AI-hint: Removes pre-LLM RAG knowledge attachments from Open WebUI models in the database to disable automatic search-query decomposition, ensuring the agent-pipe handles all logic via tool calls.
AI-related: mios-agent, mios-open-webui, mios-open-webui.service

<!-- mios-src:e6c6ffba7626 from automation/support/detach-knowledge-from-model.sh:1-3 -->

### !/bin/bash AI-hint: Force-reindexes all files in every OWUI...

!/bin/bash
AI-hint: Force-reindexes all files in every OWUI knowledge collection by cycling through /api/v1/knowledge/{id}/file/add endpoints to bypass metadata-only updates and trigger full chunking/embedding.
AI-related: /usr/libexec/mios/mios-knowledge-search, mios-knowledge-search, localhost (port key `open_webui`)

<!-- mios-src:12971b9b3441 from automation/support/force-revectorize.sh:1-3 -->

### !/bin/bash AI-hint: Executes a recovery sequence to...

!/bin/bash
AI-hint: Executes a recovery sequence to redeploy firstboot binaries, apply environment drop-ins for mios-gateway-agent, restart the agent, and verify the status of ttyd and skills-miner services.
AI-related: /usr/libexec/mios/mios-hermes-firstboot, mios-hermes-firstboot, mios-paths-env, mios-ttyd-bash, mios-ttyd-powershell, mios-skills-miner, mios-gateway-agent.service, skills-miner.timer, mios-hermes-firstboot.service, mios-skills-miner.timer

<!-- mios-src:e129a2b2a65f from automation/support/heal-all-services.sh:1-3 -->

### !/usr/bin/env python3 AI-hint: Post-build build script that...

!/usr/bin/env python3
AI-hint: Post-build build script that enforces Architectural Law 7 (OFFLINE-FIRST) by scanning the Hermes dashboard web distribution and replacing all googleapis.com font URLs with inert data URIs to prevent runtime external network requests.
AI-functions: main

<!-- mios-src:b93c52820ca1 from automation/support/hermes-dashboard-strip-externals.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Patch script for...

!/usr/bin/env python3
AI-hint: Patch script for gateway/platforms/discord.py that injects a background asyncio task to cycle Discord reactions (📡, 🧠, 🛠️, ⏳) during agent processing to provide the operator with visual progress updates.
AI-functions: _react_progression, on_processing_start, on_processing_complete, _find_target_block, main

<!-- mios-src:3e4bfc4da47c from automation/support/hermes-discord-reactions-patch.py:1-3 -->

### !/usr/bin/env bash AI-hint: Build-time script that fetches...

!/usr/bin/env bash
AI-hint: Build-time script that fetches and installs the OpenUI generative-UI bundle (JS/CSS) into /usr/share/mios/openui to ensure offline-first availability for the OWUI Tool.
AI-related: /usr/share/mios/openui, /usr/share/mios/openui/., /usr/share/mios/vendored/, mios-vendor-openui

<!-- mios-src:50c4c4b13d0c from automation/support/mios-vendor-openui.sh:1-3 -->

### !/bin/bash AI-hint: A diagnostic script that extracts the...

!/bin/bash
AI-hint: A diagnostic script that extracts the Open WebUI admin token from the local SQLite DB to probe the knowledge base API endpoints, verify retrieval functionality, and list available OpenAPI paths for RAG operations.
AI-related: localhost (port key `open_webui`)

<!-- mios-src:323b78c0ef24 from automation/support/probe-owui-knowledge-api2.sh:1-3 -->

### !/bin/bash AI-hint: This script updates the `webui.db`...

!/bin/bash
AI-hint: This script updates the `webui.db` database to link "MiOS Session Memory" and "MiOS Documentation" knowledge IDs to the `mios-agent` model metadata, enabling full RAG capabilities for the OWUI interface.
AI-related: mios-agent, mios-open-webui, mios-open-webui.service

<!-- mios-src:0a7e2280316b from automation/support/reattach-knowledge.sh:1-3 -->

### !/bin/bash AI-hint: Triggers a full re-vectorization of...

!/bin/bash
AI-hint: Triggers a full re-vectorization of Open WebUI knowledge collections via the /api/v1/knowledge/reindex endpoint to rebuild ChromaDB collections after a cache wipe or data migration.
AI-related: /usr/libexec/mios/mios-knowledge-search, mios-knowledge-search, mios-cache-clear, localhost (port key `open_webui`)

<!-- mios-src:1004d965c7de from automation/support/reindex-knowledge.sh:1-3 -->

### !/bin/bash AI-hint: Read-only health dashboard: lists...

!/bin/bash
AI-hint: Read-only health dashboard: lists systemd --failed units, prints active/enabled state for the full mios-* + hermes/owui/searxng/forge service set, tails hermes-firstboot and hermes-agent journals, and checks the canonical agent ports resolved from the `[ports]` SSOT (`agent_pipe`, `hermes`, `llm_light`).
AI-related: mios-agent-pipe, mios-daemon, mios-open-webui, mios-searxng, mios-forge, mios-skills-miner, mios-passport-provision, mios-hermes-firstboot

<!-- mios-src:7a5e4dc6270a from automation/support/service-health.sh:1-3 -->

### !/bin/bash AI-hint: Summarizes the operational status of...

!/bin/bash
AI-hint: Summarizes the operational status of core MiOS services, identifies failed systemd units, and audits active network port listeners to provide a snapshot of the system's health and connectivity.
AI-related: mios-agent-pipe, mios-daemon, mios-open-webui, mios-searxng, mios-forge, mios-skills-miner, mios-passport-provision, mios-hermes-firstboot

<!-- mios-src:724378ef261b from automation/support/service-state-compact.sh:1-3 -->

### !/bin/bash AI-hint: A smoke-test script to verify the MCP...

!/bin/bash
AI-hint: A smoke-test script to verify the MCP server's health by validating HTTP endpoints (/v1/verbs, /v1/dispatch) and stdio JSON-RPC interactions (initialize, tools/list, tools/call) for integration testing.
AI-related: /usr/libexec/mios/mios-mcp-server, mios-mcp-server, localhost (port key `agent_pipe`)

<!-- mios-src:5820bb8a4fd3 from automation/support/smoke-mcp-server.sh:1-3 -->

### !/bin/bash AI-hint: Installs and activates the...

!/bin/bash
AI-hint: Installs and activates the mios-suggestion-refresh systemd service/timer, sets permissions for the firstboot binary, and verifies that prompt suggestions are successfully populated in the webui.db database.
AI-related: /usr/libexec/mios/mios-hermes-firstboot, /usr/libexec/mios/mios-suggestion-refresh, mios-suggestion-refresh, mios-hermes-firstboot, mios-suggestion-refresh.service, mios-suggestion-refresh.timer

<!-- mios-src:81d42134d423 from automation/support/verify-starter-chips.sh:1-3 -->

### !/bin/bash AI-hint: Validates ttyd service drop-ins...

!/bin/bash
AI-hint: Validates ttyd service drop-ins, triggers the mios-hermes-firstboot service to apply systemd configurations, and verifies filesystem permissions for hermes-related directories.
AI-related: /usr/libexec/mios/mios-hermes-firstboot, mios-hermes-firstboot, mios-hermes, mios-ttyd-bash, mios-ttyd-powershell, mios-user, mios-hermes-firstboot.service

<!-- mios-src:09379c93eca8 from automation/support/verify-ttyd-userdropin.sh:1-3 -->

### !/bin/bash AI-hint: Polls the hermes-agent.service status...

!/bin/bash
AI-hint: Polls the hermes-agent.service status to bypass long gateway drain timeouts and logs the Discord patch status to verify successful configuration application during restart cycles.
AI-related: /usr/lib/mios/agents/.venv/lib/python3.14/site-packages/gateway/platforms/discord.py, hermes-agent.service

<!-- mios-src:af277b078720 from automation/support/wait-hermes-settle.sh:1-3 -->

