<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### AI-hint

AI-hint: Ensures world-readable permissions on /usr/lib/containers/storage via a systemd oneshot to prevent permission denied errors for unprivileged podman, flatpak, and GUI tools accessing the baked-in OCI image store.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:2e4d946f31b9 from usr/lib/systemd/system/mios-additionalimagestores-perms.service:1-2 -->

### AI-hint

AI-hint: Systemd unit defining the agent-pipe FastAPI service which acts as a router, refiner, and critic for the Hermes gateway, routing inference requests to the llama.cpp light lane (mios-llm-light); the lane port is the single SSOT key...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:20c8fd7aa24e from usr/lib/systemd/system/mios-agent-pipe.service:1-2 -->

### AI-hint

AI-hint: Timer that re-triggers mios-ai-firstboot.service on a cadence until the .ai-firstboot-done sentinel exists, replacing the unit's former Restart=on-failure retry storm so a partial (network-less) AI provision degrades open instead...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:c2564a1febbb from usr/lib/systemd/system/mios-ai-firstboot.timer:1-2 -->

### AI-hint

AI-hint: Systemd unit to regenerate AIOS-native role systems from the SSOT and refresh the A2A fleet peer list (explicit nodes + mDNS/avahi discovery), providing the agent-pipe with updated verb catalogs and peer discovery.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:8ed4c988e461 from usr/lib/systemd/system/mios-aios-refresh.service:1-2 -->

### AI-hint

AI-hint: Systemd unit that triggers bootc switch via /usr/libexec/mios/bootc-switch-from-build.sh when a build sentinel in /var/lib/mios/forge-runner/last-build.txt is updated, automating the transition to a new image.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:48c7ca4c6cb9 from usr/lib/systemd/system/mios-bootc-switch.service:1-2 -->

### AI-hint

AI-hint: Systemd unit file defining the core MiOS daemon; it consolidates log watching, cron gating, and agent nudging into a single process using a local qwen3 model to update the state.json file used by the OWUI sidecar.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:e464a6f8ea25 from usr/lib/systemd/system/mios-daemon.service:1-2 -->

### AI-hint

AI-hint: Defines the mios-desktop.target systemd unit to initialize the desktop environment, ensuring required virtualization services (libvirtd, virtnetworkd) are active while preventing concurrent headless or cluster-specific targets.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:8381e7b6d385 from usr/lib/systemd/system/mios-desktop.target:1-2 -->

### AI-hint

AI-hint: Day-N+1 documentation pass -- scrapes the source comments, sanitizes them and distils them into the manual, then re-renders every derived section; AI-hints are left in place and reach the docs through the index deriver instead.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:944207681067 from usr/lib/systemd/system/mios-doc-distill.service:1-2 -->

### AI-hint

AI-hint: Systemd unit to host the fine-tuned refiner model as an OpenAI /v1-compatible endpoint on port 11438, allowing the agent-pipe to swap between the high-quality transformer-served adapter and the faster llama.cpp path.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:d71cfaf17dc2 from usr/lib/systemd/system/mios-finetune-serve.service:1-2 -->

### AI-hint

AI-hint: Ensures critical MiOS service ports (resolved from the `[ports]` SSOT: open_webui, hermes, searxng, cockpit and peers, plus DNS 53) are opened in firewalld at boot to prevent connectivity loss for Open WebUI, Hermes, Cockpit, and...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:21bc62782206 from usr/lib/systemd/system/mios-firewall-ports.service:1-2 -->

### AI-hint

AI-hint: One-shot systemd service that executes the initial registration of the Forgejo runner using the local token if the runner is not yet configured, ensuring the runner is registered before the main service starts.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:d99b9fdae337 from usr/lib/systemd/system/mios-forgejo-runner-firstboot.service:1-2 -->

### AI-hint

AI-hint: Systemd unit that executes mios-freeipa-enroll.sh to perform zero-touch FreeIPA enrollment for WSL and non-containerized environments, triggered only if /etc/mios/ipa-enroll.env exists and /etc/ipa/default.conf is missing.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:07aeada037ca from usr/lib/systemd/system/mios-freeipa-enroll.service:1-2 -->

### AI-hint

AI-hint: Systemd unit for a SECOND headless ChromeDev flatpak providing a dedicated CDP endpoint at 127.0.0.1:9223 (own profile dir profile-w2) for the Hermes WORKER (:8643), so the worker's browser_* tool loop never stomps the primary...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:8873c6e19ee0 from usr/lib/systemd/system/mios-hermes-browser-worker.service:1-2 -->

### AI-hint

AI-hint: Initializes the Hermes gateway by generating the api.env file and ensuring the config.yaml matches the current schema, acting as a self-healing pre-boot step to provide required credentials and configuration for...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:01e325472f7e from usr/lib/systemd/system/mios-hermes-firstboot.service:1-2 -->

### AI-hint

AI-hint: Systemd unit that tails the hermes-agent journal to extract in-flight task/tool events into /var/lib/mios/hermes-tail/latest.json, enabling the OWUI mios_sidecar to broadcast real-time agent status to the UI.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:fc0a6c50004a from usr/lib/systemd/system/mios-hermes-tail.service:1-2 -->

### AI-hint

AI-hint: Enforces world exec+read (go+rX) perms on /usr/libexec/mios via a systemd oneshot at early boot, so MiOS services that run as a non-owner user (mios-ai, mios-skills, mios-hermes, ...) can execute their libexec scripts instead of...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:79ebf81f717b from usr/lib/systemd/system/mios-libexec-perms.service:1-2 -->

### AI-hint

AI-hint: Systemd unit file defining the mios-mcp.service daemon, which provides the Model Context Protocol (MCP) server for autonomous agents to access system context via a hardened, high-availability userspace listener.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:2ff4ba0c7276 from usr/lib/systemd/system/mios-mcp.service:1-2 -->

### AI-hint

AI-hint: One-shot first-boot Machine Owner Key (MOK) enrollment so the baked signed-UKI (rendered by automation/76-uki-render.sh + tools/generate-uki-cmdline.py) verifies under ENFORCING Secure Boot on the INSTALLED disk; mirrors...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:0303416412e5 from usr/lib/systemd/system/mios-mok-enroll.service:1-2 -->

### AI-hint

AI-hint: Systemd unit that hosts the opencode-gateway on the `opencode_gateway` port, providing a standard OpenAI-compatible /v1 API shim for the opencode CLI to enable multi-agent fan-out and local inference via mios-llm-light.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:e6a05aed9e28 from usr/lib/systemd/system/mios-opencode-gateway.service:1-2 -->

### AI-hint

AI-hint: Unprivileged daily oneshot that pg_dumps the unified agent-plane Postgres+pgvector database to /var/lib/mios/backups over loopback-trust and prunes to the newest MIOS_PGVECTOR_BACKUP_KEEP snapshots; degrade-open so a backup failure...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:a3b862f16f86 from usr/lib/systemd/system/mios-pgvector-backup.service:1-2 -->

### AI-hint

AI-hint: Ordered-before oneshot that lets the pgvector image float across PostgreSQL majors -- it dumps an older cluster with the older image into the initdb restore slot and stashes the old data dir so the new major can initialise, and...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:5b8d079a7f48 from usr/lib/systemd/system/mios-pgvector-major-upgrade.service:1-2 -->

### AI-hint

AI-hint: Systemd unit for the WS-9 out-of-process HITL policy arbiter -- runs /usr/libexec/mios/mios-policy-arbiter (a stdlib loopback HTTP service) as the mios-ai user, answering the agent-pipe's HITL arbiter client with allow/deny...
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:b1518ad93d63 from usr/lib/systemd/system/mios-policy-arbiter.service:1-2 -->

### AI-hint

AI-hint: Systemd unit that executes `mios-skills mine` to process the pgvector tool_call history into a skill catalog, automatically updating the shared catalog and reaping low-performing skills via `export-catalog` and `reap`.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:983ceb46c820 from usr/lib/systemd/system/mios-skills-miner.service:1-2 -->

### AI-hint

AI-hint: Systemd unit that executes /usr/libexec/mios/mios-sys-env to probe and UPSERT the current system state (apps, services, models, hardware) into the pgvector sys_env:current row for global agent synchronization.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:b78b75c12781 from usr/lib/systemd/system/mios-sys-env-refresh.service:1-2 -->

### AI-hint

AI-hint: Systemd timer that triggers the mios-sys-env-refresh service every 900 seconds to synchronize the sys_env environment cache with current system state, ensuring shared snapshots reflect recent app/service changes.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:a0902f99cee1 from usr/lib/systemd/system/mios-sys-env-refresh.timer:1-2 -->

### AI-hint

AI-hint: A systemd oneshot service that enforces 0700 permissions on /mnt/wslg/runtime-dir to enable Wayland VAIL mode and replaces the /tmp/.X11-unix symlink with a physical directory to ensure Flatpak/bwrap compatibility.
AI-doc: usr/share/doc/mios/manual/system.md

<!-- mios-src:902f8ea90c13 from usr/lib/systemd/system/mios-wslg-permissions-fix.service:1-2 -->
