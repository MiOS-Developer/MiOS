<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### !/usr/bin/env bash AI-hint: Executes the...

!/usr/bin/env bash
AI-hint: Executes the mios-cpu-isolate.service to parse isolcpus= kernel parameters and migrate all user-space processes onto non-isolated host cores via taskset to ensure deterministic performance.
AI-related: /usr/lib/mios/paths.sh, mios-cpu-isolate, mios-cpu-isolate.service, multi-user.target
AI-functions: _log, expand

<!-- mios-src:4225578bc2f1 from usr/libexec/mios/cpu-isolate:1-4 -->

### !/bin/sh AI-hint: A robust wrapper for flatpak run that...

!/bin/sh
AI-hint: A robust wrapper for flatpak run that restores missing WSLg, Wayland, X11, and PulseAudio environment variables (DISPLAY, WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS) lost during shell transitions like su, sudo, or nsenter; the launch is always DETACHED so its "dispatched" line is NOT a success confirmation -- the caller (mios-gui) does the authoritative new-window poll.
AI-related: /usr/libexec/mios/flatpak-launch, /usr/libexec/mios/mios-autocenter, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-as-operator, /usr/libexec/mios/mios-wsl-flatpak-heal, /etc/mios/flatpak-flags/, /usr/share/mios/flatpak-flags/, /etc/mios/wsl-distro, mios-autocenter
AI-functions: _mios_fire_autocenter, _q

<!-- mios-src:15c533b46d1f from usr/libexec/mios/flatpak-launch:1-4 -->

### !/bin/bash AI-hint: Detects virtualization status to...

!/bin/bash
AI-hint: Detects virtualization status to automatically toggle NVIDIA driver blacklisting and configure the GSK_RENDERER environment for optimal software fallback in VMs or hardware acceleration on bare metal.
AI-related: mios-gpu-detect, mios-virt-gpu, mios-renderer, mios-nvidia-blacklist, mios-gpu-detected

<!-- mios-src:558f6c5a0642 from usr/libexec/mios/gpu-detect:1-3 -->

### !/usr/bin/env bash AI-hint: Detects Hyper-V GPU...

!/usr/bin/env bash
AI-hint: Detects Hyper-V GPU paravirtualization, WSLg, and NVIDIA guest status to write /run/mios/gpu-pv.status, enabling the MiOS GPU stack to toggle between hardware-accelerated rendering and llvmpipe.
AI-related: /usr/lib/mios/paths.sh, mios-gpu-pv-detect, mios-gpu-pv-detect.service
AI-functions: _log

<!-- mios-src:a7feb3331473 from usr/libexec/mios/gpu-pv-detect:1-4 -->

### !/usr/bin/env bash AI-hint: First-boot seeder for the...

!/usr/bin/env bash
AI-hint: First-boot seeder for the non-thin Hermes WORKER (:8643). Ensures /var/lib/mios/hermes-worker/config.yaml exists from the vendor template /usr/share/mios/hermes/config-worker.yaml, owned by mios-ai. Copy-if-absent + heal-on-vendor-update; NEVER touches the primary `hermes` config /var/lib/mios/hermes/config.yaml (mios-hermes-firstboot owns/re-thins it every boot).
AI-related: /usr/share/mios/hermes/config-worker.yaml, /var/lib/mios/hermes-worker/config.yaml, hermes-worker.service, mios-hermes-firstboot, mios-ai
AI-functions: _log

<!-- mios-src:8182a0b2da4a from usr/libexec/mios/hermes-worker-firstboot:1-4 -->

### !/usr/bin/env bash AI-hint: Initializes libvirt...

!/usr/bin/env bash
AI-hint: Initializes libvirt infrastructure by ensuring modular sockets are active, defining the 'mios' network and 'images' storage pool, and configuring autostart for first-boot deployment.
AI-related: /usr/lib/mios/paths.sh, mios-libvirtd-setup, mios-libvirtd-firstboot, mios-br0, mios-libvirtd-setup.service, libvirtd.service, virtqemud.socket, virtnetworkd.socket, virtstoraged.socket, virtnodedevd.socket
AI-functions: _log

<!-- mios-src:87fa995e361d from usr/libexec/mios/libvirtd-firstboot:1-4 -->

### !/usr/bin/env bash AI-hint: Executes the Model Context...

!/usr/bin/env bash
AI-hint: Executes the Model Context Protocol (MCP) server by detecting and running a custom entrypoint at /srv/ai/mcp/ or falling back to the built-in MiOS verb relay on port 8765. When invoked with arguments AND [security].mcp_sandbox=true, acts as a GATEKEEPER: validates tool arguments (blocking directory traversal), enforces MIOS_WRITE_ALLOWED_PATHS, and optionally wraps execution in a rootless podman sandbox.
AI-related: /srv/ai/mcp/server.sh, /usr/lib/mios/paths.sh, /usr/libexec/mios/mios-mcp-server, mios-mcp-server, mios-mcp, mios-mcp.service, localhost:8080
AI-functions: _log, _block_traversal, _validate_write_path, _sandbox_exec

<!-- mios-src:cb4f6e45ff21 from usr/libexec/mios/mcp-server-runner:1-4 -->

### !/usr/bin/env python3 AI-hint: Scans and validates A2A peer...

!/usr/bin/env python3
AI-hint: Scans and validates A2A peer nodes from mios.toml and CIDR ranges to populate /etc/mios/ai/v1/a2a-peers.json, ensuring the agent-pipe has a verified list of live, reachable peers for delegation.
AI-related: /etc/mios/ai/v1/a2a-peers.json, /usr/share/mios/mios.toml, /etc/mios/mios.toml, ./mios-a2a-mdns, mios-local

<!-- mios-src:b1d601179be6 from usr/libexec/mios/mios-a2a-discover:1-3 -->

### !/usr/bin/env python3 AI-hint: SSOT-driven avahi/mDNS side...

!/usr/bin/env python3
AI-hint: SSOT-driven avahi/mDNS side of A2A discovery. Renders the LAN-announce avahi service file from the /usr/lib template (port + service-type substituted from mios.toml, never hardcoded) when [a2a].mdns_advertise is set, and browses _mios-a2a._tcp with avahi-browse to surface candidate peer URLs for mios-a2a-discover to card-probe. All behaviour is flag-gated + degrade-open: a missing avahi, daemon, or template never raises.
AI-related: /usr/lib/mios/avahi/mios-a2a.service.in, /etc/avahi/services/mios-a2a.service, ./mios-a2a-discover, /usr/share/mios/mios.toml, /etc/mios/mios.toml, avahi-daemon.service, mios-aios-refresh.service
AI-functions: _load_toml, _resolve_port, _service_type, advertise, browse, reload_peers, main

<!-- mios-src:ffc17ae196ef from usr/libexec/mios/mios-a2a-mdns:1-4 -->

### !/usr/bin/env python3 AI-hint: A2A federation loopback...

!/usr/bin/env python3
AI-hint: A2A federation loopback smoke test -- drives a Message -> Task -> Artifact round-trip against the local MiOS /a2a JSON-RPC surface (MiOS talking to itself as a peer) and confirms the event table recorded the delegation chain. Live-env tool the operator runs on a booted host; degrades open with a clear message when the endpoint is unreachable.
AI-related: usr/lib/mios/agent-pipe/mios_pipe/federation/a2a.py, usr/libexec/mios/mios-a2a-delegate, usr/share/mios/tests/test-a2a-loopback.sh
AI-functions: build_message, classify_task, extract_artifact_text, jsonrpc_envelope, main

<!-- mios-src:a86de2eeadab from usr/libexec/mios/mios-a2a-test:1-4 -->

### !/usr/bin/env python3 AI-hint: Daemon script that...

!/usr/bin/env python3
AI-hint: Daemon script that synchronizes PostgreSQL-defined accounts and aliases with the host UNIX user accounts (/etc/passwd, /etc/shadow, and /etc/group).
AI-related: /usr/libexec/mios/mios-account-sync, mios-account-sync.service, mios-pg-query, account (pgvector table), aliases (pgvector table)

<!-- mios-src:c56552493dee from usr/libexec/mios/mios-account-sync:1-3 -->

### !/usr/bin/env python3 AI-hint: Generates the initial...

!/usr/bin/env python3
AI-hint: Generates the initial AdGuardHome.yaml config by merging mios.toml settings with live Tailscale network data (IPv4 and MagicDNS) to establish DNS binding and split-DNS rules during first boot.
AI-related: /etc/mios/adguard/AdGuardHome.yaml, /usr/share/mios/mios.toml, /etc/mios/mios.toml, /etc/mios/adguard, mios-adguard

<!-- mios-src:8962cfbc1abf from usr/libexec/mios/mios-adguard-firstboot:1-3 -->

### !/usr/bin/env python3 AI-hint: WS-2/WS-10 generator CLI --...

!/usr/bin/env python3
AI-hint: WS-2/WS-10 generator CLI -- regenerates (or --check verifies) the UNIFIED RBAC capability manifest ai/v1/capabilities.generated.json from the live mios.toml [verbs.*] + [recipes.*] SSOT, via the pure mios_capreg core (projected at ceiling=interactive = the full known-tier surface). Default writes the manifest; --check regenerates in-memory and DIFFS the committed file, exiting 1 on drift so 98-drift-checks / CI fail when the committed projection no longer matches the SSOT. Mirrors mios-ai-manifest-gen (verb-only). Standalone (adds the agent-pipe dir to sys.path); no server.py, no DB, no network.
AI-related: /usr/lib/mios/agent-pipe/mios_capreg.py, /usr/share/mios/mios.toml, /usr/share/mios/ai/v1/capabilities.generated.json, ./mios-ai-manifest-gen
AI-functions: _doc, main

<!-- mios-src:7d876e512f44 from usr/libexec/mios/mios-ai-capabilities-gen:1-4 -->

### !/bin/bash AI-hint: Executes a Day-0 global reset of the...

!/bin/bash
AI-hint: Executes a Day-0 global reset of the MiOS AI stack by purging all transient runtime states, Hermes cron jobs, OWUI data, and pgvector agent caches while preserving core identities and configurations.
AI-related: /usr/libexec/mios/mios-ai-clear, /usr/libexec/mios/mios-rag, mios-rag, mios-ai-reset, mios-agent-pipe, mios-daemon, mios-log-watcher, mios-cron-director, mios-hermes-browser, mios-open-webui
AI-functions: sec, ok, note, run

<!-- mios-src:aea0f7a7aa48 from usr/libexec/mios/mios-ai-clear:1-4 -->

### !/usr/bin/env bash AI-hint: Provisioning script that...

!/usr/bin/env bash
AI-hint: Provisioning script that installs the hermes-agent Python venv and extracts llama.cpp GGUF models to enable AI services, creating a sentinel file to gate network-less boot retries.
AI-related: /usr/share/mios/llamacpp/mios-llm-light.yaml, /usr/lib/mios/agents/.venv, /usr/share/mios/llamacpp/models, /usr/libexec/mios/72-hermes-agent.sh, /etc/mios/containers/coderun-sandbox, mios-coderun-sandbox, mios-coderun-session, mios-ai, mios-agent-pipe, mios-llm-light
AI-functions: log

<!-- mios-src:fa5195d2b1b8 from usr/libexec/mios/mios-ai-firstboot:1-4 -->

### !/usr/bin/env python3 AI-hint: WS-A1 anti-drift generator...

!/usr/bin/env python3
AI-hint: WS-A1 anti-drift generator CLI -- regenerates (or --check verifies) the ai/v1 verb-catalog manifest projection (ai/v1/tools.generated.json) from the live mios.toml [verbs.*] SSOT, via the pure mios_manifest core in the agent-pipe. Default writes the manifest; --check regenerates in-memory and DIFFS the committed file, exiting 1 on any drift so 98-drift-checks / CI fail when the committed projection no longer matches the SSOT. Standalone (adds the agent-pipe dir to sys.path); no server.py, no DB, no network.
AI-related: /usr/lib/mios/agent-pipe/mios_manifest.py, /usr/share/mios/mios.toml, /usr/share/mios/ai/v1/tools.generated.json, /usr/share/mios/ai/v1/tools.json, ./mios-docgen
AI-functions: _generate, main

<!-- mios-src:8ff775919e06 from usr/libexec/mios/mios-ai-manifest-gen:1-4 -->

### !/bin/bash AI-hint: Wipes all non-persistent AI state (chat...

!/bin/bash
AI-hint: Wipes all non-persistent AI state (chat history, kanban, memory, and browser profiles) while preserving core configs and models to provide a clean slate for testing or new sessions.
AI-related: /usr/libexec/mios/mios-ai-reset, mios-hermes-browser, mios-open-webui, mios-log-watcher, mios-cron-director, mios-hermes, mios-agent, hermes-agent.service, mios-open-webui.service, mios-log-watcher.service
AI-functions: _check_safe, _do

<!-- mios-src:e3d43f3052b2 from usr/libexec/mios/mios-ai-reset:1-4 -->

### !/usr/bin/env python3 AI-hint: Provides semantic search...

!/usr/bin/env python3
AI-hint: Provides semantic search over the mios-apps inventory via the agent-pipe endpoint to resolve ambiguous natural-language queries into specific app metadata for agent-driven actions.
AI-related: mios-apps, agent-pipe (port key `agent_pipe`)

<!-- mios-src:4e494d743d55 from usr/libexec/mios/mios-app-search:1-3 -->

### !/bin/bash AI-hint: Provides a unified inventory of all...

!/bin/bash
AI-hint: Provides a unified inventory of all launchable entities (Flatpaks, RPMs, Windows apps, shims, and service URLs) across all environments, used by agents to discover and target specific applications for execution.
AI-related: /usr/libexec/mios/mios-apps, mios-shim, mios-launch, mios-gui, mios-windows, mios-windows-apps, mios-hermes, mios-find, mios-open-url, localhost:9090
AI-functions: emit, _jsonesc, _is_wsl, header

<!-- mios-src:c77fbeace7f3 from usr/libexec/mios/mios-apps:1-4 -->

### !/bin/bash AI-hint: Executes commands in a fresh WSL login...

!/bin/bash
AI-hint: Executes commands in a fresh WSL login session as the operator user to bootstrap the full WSLg environment (Wayland, user-bus, and interop) required for GUI applications and Flatpaks to function correctly.
AI-related: /usr/libexec/mios/mios-as-operator, /usr/share/mios/mios.toml, /etc/mios/wsl-distro, mios-launcher, mios-hermes, 1000.service, mios-launcher.socket, mios-launcher.service, socket.socket
AI-functions: _quote

<!-- mios-src:22474a7f72b1 from usr/libexec/mios/mios-as-operator:1-4 -->

### !/bin/sh AI-hint: Executes a polling loop to identify and...

!/bin/sh
AI-hint: Executes a polling loop to identify and center newly mapped windows (WSLg/Flatpak) by comparing current HWNDs against a pre-launch snapshot via the os_control executor to ensure correct placement of main application windows.
AI-related: /usr/libexec/mios/mios-autocenter

<!-- mios-src:db0344f5cf7f from usr/libexec/mios/mios-autocenter:1-3 -->

### !/usr/bin/env bash AI-hint: MiOS configuration and runtime...

!/usr/bin/env bash
AI-hint: MiOS configuration and runtime asset for mios-bake-group.
AI-related: /usr/share/mios/mios.toml, /usr/share/mios/artifacts/sbom, /usr/lib/mios/bake/plan.d, mios-bakescratch, mios-sys-build, mios-webtools-firstboot, mios-webtools-firstboot.service

<!-- mios-src:3ff9232d0e45 from usr/libexec/mios/mios-bake-group:1-3 -->

### !/usr/bin/env python3 AI-hint: MiOS agentic-capability...

!/usr/bin/env python3
AI-hint: MiOS agentic-capability benchmark harness CLI. `score` (OFFLINE, pure): reads a trial-results JSON, prints the CLASSic rollup + pass@k / pass^k (tau-bench) via the tested mios_bench core. `run` (VM-gated): drives prompt suites at the agent-pipe endpoint (port key `agent_pipe`, MIOS_AI_ENDPOINT, Law-5) k times, substring-checks answers, writes the JSON `score` reads.
AI-related: /usr/lib/mios/agent-pipe/mios_bench.py, mios_bench, /usr/share/doc/mios/concepts/aios-engineering-blueprint.md, agent-pipe (port key `agent_pipe`)
AI-functions: _load_json, cmd_score, cmd_run, main

<!-- mios-src:c375ee71bb4e from usr/libexec/mios/mios-bench:1-4 -->

### !/usr/bin/env bash AI-hint: The `mios blade` verb -- the...

!/usr/bin/env bash
AI-hint: The `mios blade` verb -- the day-2 face of the blade archetype (WS-BLADE). Writes the HOST tier (/etc/mios/role.conf) and re-runs role-apply, which detects the change and activates the new role target. Every argument is validated against the SSOT before it is written: an unknown archetype or an ungrantable capability is refused with the legal set, because both used to be accepted verbatim and produced a blade that silently started nothing.
AI-related: usr/lib/mios/blade.sh, usr/libexec/mios/role-apply, /etc/mios/role.conf, /etc/mios/blade.d/, usr/share/mios/mios.toml, usr/share/doc/mios/adr/0016-blade-node-topology.md
AI-functions: log, die, show_help, _load_ssot, _require_archetype, _require_capability, _set_conf_key

<!-- mios-src:a58ba10a68c7 from usr/libexec/mios/mios-blade:1-4 -->

### !/usr/bin/env bash AI-hint: Entry point for the MiOS-DEV...

!/usr/bin/env bash
AI-hint: Entry point for the MiOS-DEV build pipeline; executes the full multi-format build process (OCI, WSL, QEMU, etc.) and renders the interactive dashboard within a Windows-hosted terminal session.
AI-related: /etc/profile.d/mios-env.sh, /usr/libexec/mios/mios-build-driver, /etc/mios/install.env, /etc/mios/secrets.env, /etc/mios/mios.toml, /usr/libexec/mios/mios-configurator-launch, /usr/share/mios/mios.toml, /usr/share/mios/lib/userenv.sh, mios-env, mios-configurator-launch
AI-functions: _log, _fail, _hash_password

<!-- mios-src:a50ff9f814c5 from usr/libexec/mios/mios-build-driver:1-4 -->

### !/bin/sh AI-hint: Retrieves and streams the most recent raw...

!/bin/sh
AI-hint: Retrieves and streams the most recent raw build log from /var/log/mios or /tmp, used by agents to inspect real-time or historical build output from the mios-build-driver.
AI-related: /usr/libexec/mios/mios-build-tail, mios-build-driver, mios-build-status

<!-- mios-src:dab06d4768cb from usr/libexec/mios/mios-build-tail:1-3 -->

### !/bin/bash AI-hint: Executes a safe, selective purge of...

!/bin/bash
AI-hint: Executes a safe, selective purge of non-essential OWUI, Hermes, and system cache data while preserving critical auth, config, and model assets to reset state without causing user lockouts or data loss.
AI-related: /usr/libexec/mios/mios-cache-clear, mios-hermes, mios-owui-apply, mios-open-webui, mios-owui-apply-knowledge, hermes-agent.service, mios-open-webui.service
AI-functions: ok, note, sec, size, do_rm, do_find_delete

<!-- mios-src:e8272f0b07da from usr/libexec/mios/mios-cache-clear:1-4 -->

### !/usr/bin/env bash AI-hint: Detects GPU hardware (NVIDIA...

!/usr/bin/env bash
AI-hint: Detects GPU hardware (NVIDIA, AMD, Intel) and generates corresponding CDI specification files in /run/cdi/ to enable containerized GPU access before the nvidia-cdi-refresh service.
AI-related: /usr/lib/mios/paths.sh, /usr/libexec/mios/intel-cdi-specs-generator, mios-gpu, mios-boot-diag, nvidia-cdi-refresh.service, mios-cdi-detect.service
AI-functions: _log

<!-- mios-src:23906ebb7cb8 from usr/libexec/mios/mios-cdi-detect:1-4 -->

### !/usr/lib/mios/agents/.venv/bin/python3 AI-hint: Fetches...

!/usr/lib/mios/agents/.venv/bin/python3
AI-hint: Fetches rendered text and title from a URL via the Chrome DevTools Protocol (CDP) on port 9222 to provide the agent with grounded, non-hallucinated DOM content instead of predicted text.
AI-related: /usr/lib/mios/agents/.venv/bin/python3, mios-hermes-browser
AI-functions: main

<!-- mios-src:e64314ba950b from usr/libexec/mios/mios-cdp-fetch:1-4 -->

### !/usr/bin/env python3 AI-hint: Automated client cache...

!/usr/bin/env python3
AI-hint: Automated client cache configuration utility for CephFS, rendering performance options into /etc/ceph/ceph.conf.
AI-related: /etc/ceph/ceph.conf, /usr/share/mios/mios.toml, automation/firstboot/mios-cephfs-mount-setup.sh
AI-functions: load_cephfs_config, configure_client, main

<!-- mios-src:fb5c4d9dc7c8 from usr/libexec/mios/mios-ceph-configure:1-4 -->

### !/usr/bin/env python3 AI-hint: Automated provisioning...

!/usr/bin/env python3
AI-hint: Automated provisioning utility for CephFS user home subvolumes and path-scoped CephX keyrings.
AI-related: /usr/lib/pam.d/system-auth, /usr/libexec/mios/mios-pg-query, /usr/share/mios/mios.toml
AI-functions: load_cephfs_config, get_user_info, run_cmd, log_event, cmd_validate, cmd_create, cmd_delete, main

<!-- mios-src:2848458faf57 from usr/libexec/mios/mios-cephfs-provision:1-4 -->

### !/usr/bin/env python3 AI-hint: SEC-03 CLI that verifies the...

!/usr/bin/env python3
AI-hint: SEC-03 CLI that verifies the tamper-evident SHA-256 hash chain over the agent-plane `event` table. Reads every chained row (WHERE chain_hash IS NOT NULL) in chain_seq order via mios-pg-query's parameterized --exec-json transport (pure pg wire protocol -- no psql/psycopg/podman, works for confined users), then recomputes each link with the SSOT algorithm imported from the agent-pipe's mios_audit module (same canonical_core + sha256 used at write time, so there is no second copy of the crypto to drift). Reports {ok, checked, first_broken_seq} as JSON and EXITS NONZERO on a broken chain (1 = tamper detected at first_broken_seq, 2 = read/setup failure) so it drops into a cron/audit gate. A clean or empty chain is exit 0.
AI-related: ../../lib/mios/agent-pipe/mios_audit.py, ./mios-pg-query, ./mios-remember, ../../share/mios/postgres/schema-init.sql
AI-functions: _agent_pipe_dir, _read_chain_rows, main

<!-- mios-src:00c8758f10f1 from usr/libexec/mios/mios-chain-verify:1-4 -->

### !/usr/bin/env python3 AI-hint: Executes agent-provided bash...

!/usr/bin/env python3
AI-hint: Executes agent-provided bash or python snippets within a restricted bubblewrap sandbox, providing a safe, ephemeral execution environment with controlled network access and a private scratch workspace.
AI-related: mios-sandbox-exec, mios-coderun-session

<!-- mios-src:005ec1c8c6c3 from usr/libexec/mios/mios-coderun:1-3 -->

### !/usr/bin/env python3 AI-hint: Executes agent-supplied code...

!/usr/bin/env python3
AI-hint: Executes agent-supplied code snippets within a hardened, persistent Podman container sandbox to provide a "Code Mode" tool interface that reduces context window usage by replacing numerous function schemas with a single shim.
AI-related: /usr/lib/mios/agent-pipe, /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-coderun, mios-coderun-session, mios-coderun-sandbox

<!-- mios-src:1d47bc365244 from usr/libexec/mios/mios-coderun-codemode:1-3 -->

### !/bin/bash AI-hint: Orchestrates the lifecycle of...

!/bin/bash
AI-hint: Orchestrates the lifecycle of agent-specific coderun sandboxes by managing systemd user units, btrfs snapshots, and git stashes for project isolation and state recovery based on a unique session ID.
AI-related: /usr/libexec/mios/mios-coderun-session, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-coderun-sandbox
AI-functions: usage, _resolve_projects_root, _resolve_snapshots_root, _require_session, _is_btrfs, _snap_btrfs, _snap_git_stash, do_start, do_stop, do_snap, do_revert, do_status

<!-- mios-src:fc19e1da4a2c from usr/libexec/mios/mios-coderun-session:1-4 -->

### !/usr/bin/env python3 AI-hint: Compacts recent agent...

!/usr/bin/env python3
AI-hint: Compacts recent agent interactions, launch failures, and system logs into a timestamped markdown digest in /var/lib/mios/compacted/ to be ingested as an OWUI knowledge artifact for RAG.
AI-related: mios-knowledge-add, mios-daemon
AI-functions: section_launch_failures, section_daemon_state

<!-- mios-src:46c5c7e86e30 from usr/libexec/mios/mios-compact:1-4 -->

### !/usr/bin/env python3 AI-hint: Executes Linux/Wayland...

!/usr/bin/env python3
AI-hint: Executes Linux/Wayland desktop interactions via RemoteDesktop portal, uinput, or WSL-delegated Windows control, providing a unified `cu_*` verb interface for remote/local UI automation and vision grounding.
AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-pc-control, mios-pc-vision, mios-grounding, mios-computer-use-server, mios-oscontrol-server, mios-cu

<!-- mios-src:278c9640f5be from usr/libexec/mios/mios-computer-use:1-3 -->

### !/usr/bin/env python3 AI-hint: Provides a dual-protocol...

!/usr/bin/env python3
AI-hint: Provides a dual-protocol (MCP/A2A) and REST-compliant FastAPI server that exposes local desktop automation tools, window management, and input injection as a federated capability for the central agent-pipe.
AI-related: /usr/lib/mios/agents/.venv, /etc/mios/ai/v1/mcp.json, /etc/mios/ai/v1/a2a-peers.json, /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-computer-use, mios-oscontrol-server, mios-token

<!-- mios-src:d4bb40b5c143 from usr/libexec/mios/mios-computer-use-server:1-3 -->

### !/usr/bin/env bash AI-hint: Opens the unified MiOS Settings...

!/usr/bin/env bash
AI-hint: Opens the unified MiOS Settings surface. PRIMARY target is the configurator embedded in the MiOS Portal at /configure on the `agent_pipe` port (probed with curl); only when the Portal is unreachable does it fall back to the standalone HTML configurator staged in ~/Downloads and opened via file:// (offline / USB, ADR-0008).
AI-related: /usr/libexec/mios/mios-configurator-launch, /usr/share/mios/configurator/mios.html, /configure on the `agent_pipe` port, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-configurator, mios-open-url, mios-rebuild

<!-- mios-src:fc716f39d729 from usr/libexec/mios/mios-configurator-launch:1-3 -->

### !/usr/bin/env python3 AI-hint: Python script providing a...

!/usr/bin/env python3
AI-hint: Python script providing a thin client to the local crawl4ai service to fetch and convert web pages into LLM-ready markdown, used by agents to ground responses in actual content rather than search snippets.
AI-related: mios-crawl4ai, mios-web-search, mios-hermes-browser, mios-web-extract, mios-hermes-browser.service, mios-crawl4ai.service

<!-- mios-src:8d2dd04352b7 from usr/libexec/mios/mios-crawl:1-3 -->

### !/usr/bin/env python3 AI-hint: A cron-task scheduler that...

!/usr/bin/env python3
AI-hint: A cron-task scheduler that parses system and user rules from TOML files, executing commands via bash while optionally gating execution through a local LLM's YES/NO decision based on system state.
AI-related: /etc/mios/cron-rules.toml, mios-ai

<!-- mios-src:16a41a1ab533 from usr/libexec/mios/mios-cron-director:1-3 -->

### !/usr/bin/env python3 AI-hint: CLI tool for managing...

!/usr/bin/env python3
AI-hint: CLI tool for managing cron-director rules by translating human-readable intervals into cron expressions, storing prompt text in /var/lib/mios/cron-director/prompts, and updating /etc/mios/cron-rules.toml.
AI-related: /etc/mios/cron-rules.toml., /etc/mios/cron-rules.toml, mios-scheduled-research, mios-cron-director, mios-ai, mios-cron-director.service
AI-functions: _reload_daemon

<!-- mios-src:edcb4bffe8d0 from usr/libexec/mios/mios-cron-schedule:1-4 -->

### !/usr/bin/env python3 AI-hint: Visual Definition-of-Done...

!/usr/bin/env python3
AI-hint: Visual Definition-of-Done tool for PC-CONTROL. Takes a screenshot and asks the vision model if a specified condition (e.g., "is the terminal open?") is met on screen. Returns {ok: bool, reasoning: text}.
AI-related: /usr/share/mios/mios.toml, mios-computer-use, mios-pc-vision

<!-- mios-src:468086dba7ce from usr/libexec/mios/mios-cu-verify:1-3 -->

### !/usr/bin/env python3 AI-hint: Sets the X11 root-window...

!/usr/bin/env python3
AI-hint: Sets the X11 root-window default cursor via XDefineCursor to ensure consistent cursor themes across GTK4/Xwayland windows where explicit cursor names are missing, using settings from mios.toml.
AI-related: /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-cursor

<!-- mios-src:8991ae7a68c6 from usr/libexec/mios/mios-cursor-apply:1-3 -->

### !/bin/bash AI-hint: Ensures the global system cursor theme...

!/bin/bash
AI-hint: Ensures the global system cursor theme (Bibata) is correctly installed and linked in /usr/share/icons or ~/.local/share/icons based on available privileges to guarantee consistent cursor rendering across X11, Wayland, and Flatpak apps.
AI-related: /usr/libexec/mios/mios-cursor-ensure, /etc/mios/mios.toml, /usr/share/mios/mios.toml
AI-functions: _toml_get, _write_default_pointer, _wire

<!-- mios-src:8aec527c9bb2 from usr/libexec/mios/mios-cursor-ensure:1-4 -->

### !/usr/bin/env python3 AI-hint: Consolidated MiOS core...

!/usr/bin/env python3
AI-hint: Consolidated MiOS core daemon that unifies log classification, refusal detection, and cron task evaluation into a single llama.cpp /v1-backed process, outputting a unified state.json for the OWUI sidecar filter.
AI-related: /etc/mios/daemon/cron.toml, /usr/share/mios/mios.toml, /etc/mios/mios.toml, /usr/libexec/mios/mios-db, /etc/mios/daemon/cron.toml., mios-db, mios-log-watcher, mios-agent-nudger, mios-cron-director
AI-functions: _atomic_write_state

<!-- mios-src:1f45bd5b225d from usr/libexec/mios/mios-daemon:1-4 -->

### !/bin/bash AI-hint: Purges volatile runtime data (sessions...

!/bin/bash
AI-hint: Purges volatile runtime data (sessions, tool_calls, knowledge, logs) from the pgvector agent DB (via parameterized mios-db --pg) plus OWUI's sqlite chats and filesystem caches, while preserving core system configurations and identity keys to reset the AI's operational state to a clean "day-0" baseline.
AI-related: /usr/libexec/mios/mios-day0-reset, mios-db, mios-pg-query, mios-agent-pipe, mios-hermes-tail, mios-daemon, mios-mcp, mios-pgvector, mios-daemon-state, mios-ai, mios-agent-pipe.service, hermes-agent.service
AI-functions:

<!-- mios-src:0bd506b288fa from usr/libexec/mios/mios-day0-reset:1-4 -->

### !/bin/bash AI-hint: Unified MiOS shared-state CLI fronting...

!/bin/bash
AI-hint: Unified MiOS shared-state CLI fronting the agent backends: PostgreSQL/pgvector for cross-cutting state (--pg), Open WebUI's SQLite webui.db (--owui), and local OpenAI-compat embeddings on mios-llm-light (--embed).
AI-related: userenv.sh, /usr/libexec/mios/mios-db, /usr/share/mios/mios.toml, /etc/mios/mios.toml, /usr/lib/mios/userenv.sh, /usr/share/mios/tools/lib/userenv.sh, /usr/libexec/mios/mios-pg-query, mios-pg-query, mios-env, mios-open-webui

<!-- mios-src:f5cb80984b4d from usr/libexec/mios/mios-db:1-3 -->

### !/usr/bin/env python3 AI-hint: Provides high-speed (<100ms)...

!/usr/bin/env python3
AI-hint: Provides high-speed (<100ms) retrieval of the pgvector-cached directory map (parameterized pg via mios-db --pg-json) to allow agents to perform rapid file/directory lookups and navigation instead of slow filesystem crawls.
AI-related: /usr/libexec/mios/mios-db, mios-db, mios-daemon, mios-find, mios-sys-env, mios-pg-query

<!-- mios-src:f95a0f8d812b from usr/libexec/mios/mios-directory-lookup:1-3 -->

### !/usr/bin/env python3 AI-hint: Python script for sending...

!/usr/bin/env python3
AI-hint: Python script for sending deterministic Discord messages or DMs to specific users/channels, providing the orchestrator with real-time success/failure JSON results instead of hallucinated outcomes.
AI-related: /etc/mios/hermes/discord.env

<!-- mios-src:ec44bc059084 from usr/libexec/mios/mios-discord-send:1-3 -->

### !/bin/bash AI-hint: Diagnostic script for the Hermes-Agent...

!/bin/bash
AI-hint: Diagnostic script for the Hermes-Agent Discord integration that validates token validity, API connectivity, and configuration completeness to help agents and operators troubleshoot Discord notification failures.
AI-related: /usr/libexec/mios/mios-discord-status, /etc/mios/hermes/discord.env, hermes-agent.service
AI-functions: red, green, yellow, hdr

<!-- mios-src:4812c15bf8b8 from usr/libexec/mios/mios-discord-status:1-4 -->

### !/usr/bin/env bash AI-hint: Day-N+1 runner -- distils...

!/usr/bin/env bash
AI-hint: Day-N+1 runner -- distils source comments into the manual and re-renders every derived section, refusing to touch a read-only tree so a booted immutable host no-ops instead of failing.
AI-related: mios-doc-distill.service, mios-doc-distill.timer, mios-manual
AI-functions: main

<!-- mios-src:0cac139e50c8 from usr/libexec/mios/mios-doc-distill:1-4 -->

### !/usr/bin/env python3 AI-hint: A headless document...

!/usr/bin/env python3
AI-hint: A headless document generation engine using Pandoc and LibreOffice to convert markdown/text into office binaries (docx, pptx, xlsx, pdf) or perform format conversions, gated by the [computer_use].docgen_enable flag.
AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml

<!-- mios-src:f66cc631c53c from usr/libexec/mios/mios-docgen:1-3 -->

### !/bin/bash AI-hint: Generates a unified index of all system...

!/bin/bash
AI-hint: Generates a unified index of all system documentation (.md files) across MiOS directories to allow agents to discover and selectively load specific documentation into context via grep or direct path access.
AI-related: /usr/libexec/mios/mios-docs-index, /usr/share/mios/ai/, /usr/share/mios/docs/, /usr/share/mios/hermes/skills/, /usr/share/mios/cookbooks/, /usr/share/mios/prompts/, /etc/mios/system-prompts/, /usr/share/mios/ai, /usr/share/mios/docs, /usr/share/mios/hermes/skills
AI-functions: _describe, emit_index

<!-- mios-src:56143426d303 from usr/libexec/mios/mios-docs-index:1-4 -->

### !/bin/bash AI-hint: A diagnostic tool for identifying...

!/bin/bash
AI-hint: A diagnostic tool for identifying system-level failures in MiOS, checking sudo permissions, hermes-agent status, and mount-namespace escapability to troubleshoot environment issues.
AI-related: /usr/libexec/mios/mios-doctor, /usr/libexec/mios/flatpak-launch, /usr/libexec/mios/mios-build-driver, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /etc/mios/cron-rules.toml, /usr/share/mios/hermes/skills, /usr/share/mios/hermes/skills/, /usr/share/mios/ai/hermes-soul.md, mios-build-driver
AI-functions: ok, fail, warn, info

<!-- mios-src:c5b312029eb0 from usr/libexec/mios/mios-doctor:1-4 -->

### !/usr/bin/env python3 AI-hint: The operator-facing `mios...

!/usr/bin/env python3
AI-hint: The operator-facing `mios dotfiles` verb backend (ADR-0010) -- projects
AI-related: usr/bin/mios, usr/libexec/mios/mios-theme-render, usr/share/mios/mios.toml
AI-related: ./mios-theme-render, ./mios-sync-theme, ../../share/mios/mios.toml, ../../../etc/profile.d/mios-verbs.sh, ../../share/doc/mios/adr/0010-ssot-as-system-dotfiles.md
AI-functions: _run_engine, _classify, _dest_of, _print_summary, cmd_status, cmd_diff, cmd_sync, main

<!-- mios-src:a21c733ed2f7 from usr/libexec/mios/mios-dotfiles:1-5 -->

### !/usr/bin/env python3 AI-hint: The GLOBAL runtime theme +...

!/usr/bin/env python3
AI-hint: The GLOBAL runtime theme + dotfiles projector -- renders EVERY committed theme surface (the btop theme, oh-my-posh, quickshell, fastfetch, the app-shell CSS, the terminal OSC fallbacks) from the mios.toml [colors]/[theme] SSOT via token-substitution templates
AI-related: /etc/mios/mios.toml, mios-sync-theme, mios-bak, mios-theme-render, surface.target, s.target, apply.target

<!-- mios-src:65d6dded7a20 from usr/libexec/mios/mios-dotfiles-render:1-3 -->

### !/bin/bash AI-hint: Captures and formats the system's...

!/bin/bash
AI-hint: Captures and formats the system's hardware, service status, and configuration facts into brief, full, or machine-readable formats to provide the Hermes agent with deterministic environmental context.
AI-related: /usr/libexec/mios/mios-env-probe, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-apps, /usr/share/mios/ai/system.md, /usr/share/mios/ai/INDEX.md, /usr/share/mios/hermes/skills/, mios-apps, mios-hermes-init-hook, mios-delegation-prefilter, mios-open-webui
AI-functions: _toml_get, _svc_active, _count

<!-- mios-src:ee4bf7e2c82e from usr/libexec/mios/mios-env-probe:1-4 -->

### !/bin/bash AI-hint: A high-speed wrapper for the Voidtools...

!/bin/bash
AI-hint: A high-speed wrapper for the Voidtools Everything CLI that provides agents with sub-100ms access to the full Windows NTFS index for file discovery across all mounted drives.
AI-related: /usr/libexec/mios/mios-everything, /etc/mios/install.env, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-find, mios-es-err, hermes-agent.service
AI-functions: usage

<!-- mios-src:db5d4c00c25e from usr/libexec/mios/mios-everything:1-4 -->

### !/bin/bash AI-hint: Provides high-speed fuzzy lookup...

!/bin/bash
AI-hint: Provides high-speed fuzzy lookup against the cached environment inventory to resolve "launch <app>" requests into executable commands in <100ms, bypassing slow filesystem crawls.
AI-related: /usr/libexec/mios/mios-find, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-apps, mios-everything, mios-gui, mios-windows, mios-find-err, mios-hermes-browser, mios-shim

<!-- mios-src:502b6f05d83f from usr/libexec/mios/mios-find:1-3 -->

### !/usr/bin/env python3 AI-hint: Python script for...

!/usr/bin/env python3
AI-hint: Python script for hardware-agnostic LoRA/SFT fine-tuning of the MiOS model; detects CUDA/ROCm/MPS/CPU to train a local adapter from the mios-finetune-dataset and exports it as a GGUF LoRA adapter for the llama.cpp / mios-llm-light lane.
AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, /usr/share/mios/finetune/requirements.txt, mios-finetune-dataset, mios-sys-agent-ft

<!-- mios-src:cdb745125166 from usr/libexec/mios/mios-finetune:1-3 -->

### !/usr/bin/env python3 AI-hint: A script to generate a...

!/usr/bin/env python3
AI-hint: A script to generate a supervised fine-tuning (SFT) JSONL dataset by distilling a teacher model's responses to live system verbs and intent schemas into a training corpus for the MiOS role model.
AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-finetune-smoke, mios-llm-light (port key `llm_light`)

<!-- mios-src:8c8dc32e2327 from usr/libexec/mios/mios-finetune-dataset:1-3 -->

### !/usr/bin/env python3 AI-hint: A Python server providing...

!/usr/bin/env python3
AI-hint: A Python server providing OpenAI-compatible and MiOS-native chat endpoints for fine-tuned LoRA adapters, used by the agent-pipe refiner to serve specialized models on any hardware via the transformers library.
AI-related: /usr/libexec/mios/mios-finetune-serve, /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-sys-agent-ft
AI-functions: _load

<!-- mios-src:5921113be3df from usr/libexec/mios/mios-finetune-serve:1-4 -->

### !/usr/bin/env python3 AI-hint: Python script to scrape web...

!/usr/bin/env python3
AI-hint: Python script to scrape web pages via the local Firecrawl API (port 3002) to produce clean, LLM-ready markdown, providing a high-quality alternative to crawl4ai for rendering news and article content.
AI-related: mios-webtools, mios-crawl, mios-web-extract, localhost:3002

<!-- mios-src:5b52d3914741 from usr/libexec/mios/mios-firecrawl:1-3 -->

### !/bin/bash AI-hint: Agent-facing JSON-wrapped CLI for...

!/bin/bash
AI-hint: Agent-facing JSON-wrapped CLI for managing flatpak packages (search, install, upgrade, run) providing structured output for automated lifecycle management and non-interactive installation.
AI-related: /usr/libexec/mios/mios-flatpak, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-flatpaks, mios-launch, mios-fp-out-XXXXXX, mios-fp-err-XXXXXX
AI-functions: usage, _run_flatpak, _emit_envelope, _emit_json_error, _resolve_scope

<!-- mios-src:223362b7e185 from usr/libexec/mios/mios-flatpak:1-4 -->

### !/bin/bash AI-hint: Renames .svg files containing non-SVG...

!/bin/bash
AI-hint: Renames .svg files containing non-SVG data (e.g., PNGs) to .disabled-not-svg to prevent the WSLg weston compositor from crashing during RemoteApp list generation.
AI-related: /usr/libexec/mios/mios-flatpak-icon-sanitize, mios-wslg-permissions-fix, mios-wslg-permissions-fix.service

<!-- mios-src:1a1c200b45f6 from usr/libexec/mios/mios-flatpak-icon-sanitize:1-3 -->

### !/bin/bash AI-hint: Initializes system-wide flatpak...

!/bin/bash
AI-hint: Initializes system-wide flatpak overrides at first boot to grant all flatpaks read/write access to standard XDG user directories and shared themes, ensuring persistent data access in bootc-compatible environments.
AI-related: /usr/libexec/mios/mios-flatpak-init, /etc/mios/mios.toml, /usr/share/mios/mios.toml., /usr/share/mios/mios.toml, mios-flatpak-install, mios-flatpak-init.service
AI-functions: _log, _toml_get

<!-- mios-src:bb8a7bf5dfa4 from usr/libexec/mios/mios-flatpak-init:1-4 -->

### !/bin/bash AI-hint: Non-interactive wrapper for `flatpak...

!/bin/bash
AI-hint: Non-interactive wrapper for `flatpak install` that forces `--noninteractive` and `--from` flags to prevent agent hangs, while ensuring new apps inherit MiOS system-wide XDG override policies.
AI-related: /usr/libexec/mios/mios-flatpak-install, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-flatpak-init, mios-flatpak-init
AI-functions: _resolve_remote_for

<!-- mios-src:a7873543db48 from usr/libexec/mios/mios-flatpak-install:1-4 -->

### !/bin/bash AI-hint: Executes `flatpak override` to apply...

!/bin/bash
AI-hint: Executes `flatpak override` to apply global theme, portal, and cursor settings from `mios.toml` to all flatpak applications, ensuring consistent UI styling across the system.
AI-related: /usr/libexec/mios/mios-flatpak-overrides-apply, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-hermes-firstboot, mios-flatpak-init, mios-cursor-ensure
AI-functions: _toml_get

<!-- mios-src:4368b5774d12 from usr/libexec/mios/mios-flatpak-overrides-apply:1-4 -->

### !/bin/bash AI-hint: Validates if a flatpak app can...

!/bin/bash
AI-hint: Validates if a flatpak app can successfully bootstrap its sandbox by running a probe command and checking for specific stderr signatures (GPU, portal, or D-Bus errors) to provide a synchronous "fail-fast" check before launch.
AI-related: /usr/libexec/mios/mios-flatpak-preflight, mios-fp-pre-XXXXXX, mios-wsl-flatpak-heal
AI-functions: _probe

<!-- mios-src:493fa7dcd6d2 from usr/libexec/mios/mios-flatpak-preflight:1-4 -->

### !/usr/bin/env python3 AI-hint: Generates unified...

!/usr/bin/env python3
AI-hint: Generates unified, SSOT-driven SYSTEM prompts for MiOS agent roles by merging mios.toml configs, live verb/skill catalogs, and A2A peer surfaces into a single source for Modelfile and agent-pipe injection.
AI-related: /etc/mios/ai/v1/role-systems, /etc/mios/ai/v1/a2a-peers.json, /usr/share/mios/mios.toml, /etc/mios/mios.toml

<!-- mios-src:637ae4e424f8 from usr/libexec/mios/mios-gen-role-system:1-3 -->

### !/bin/bash AI-hint: Syncs Quadlet container configurations...

!/bin/bash
AI-hint: Syncs Quadlet container configurations with live CDI specifications in /run/cdi/ to automatically map the GPU vendors declared in mios.toml [gpu.cdi] onto background AI service Quadlets (mios-llm-light, vLLM heavy lanes) based on detected hardware; holds no vendor literal of its own.
AI-related: /usr/libexec/mios/mios-gpu-passthrough, /usr/libexec/mios/mios-toml-get, /usr/share/mios/mios.toml, /etc/mios/gpu-passthrough.conf, /etc/mios/gpu-passthrough.conf., mios-cdi-detect, mios-daemon, mios-igpu, mios-open-webui, mios-mycustom, mios-doctor, mios-system-status
AI-functions: log, read_gpu_matrix, build_dropin

<!-- mios-src:b47cc74b66dd from usr/libexec/mios/mios-gpu-passthrough:1-4 -->

### !/bin/bash AI-hint: A wrapper script that resolves and...

!/bin/bash
AI-hint: A wrapper script that resolves and launches flatpak applications via shims, exact IDs, or fuzzy matches, then BOUNDED-polls the OS-control executor for a newly-mapped window to honestly confirm the app surfaced (vs claiming success on a detached spawn) and exits non-zero (3) when unconfirmed.
AI-related: /usr/libexec/mios/mios-gui, /usr/libexec/mios/mios-host-launch, /usr/libexec/mios/flatpak-launch, /usr/libexec/mios/mios-autocenter, /usr/libexec/mios/mios-pc-control, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-host-launch
AI-functions: usage, _mios_osc_endpoint, _mios_window_hwnds, _mios_confirm_new_window

<!-- mios-src:1b64c6f995df from usr/libexec/mios/mios-gui:1-4 -->

### !/bin/bash AI-hint: A wrapper script that launches Linux...

!/bin/bash
AI-hint: A wrapper script that launches Linux GUI applications via WSLg by enforcing required environment variables (WAYLAND_DISPLAY, XDG_CURRENT_DESKTOP, XDG_SESSION_TYPE), detaching the process, and logging stderr to /var/log/mios/gui/.
AI-related: /usr/libexec/mios/mios-gui-launch, /usr/libexec/mios/flatpak-launch, mios-window-active, mios-find
AI-functions: usage

<!-- mios-src:cd516baed868 from usr/libexec/mios/mios-gui-launch:1-4 -->

### !/usr/bin/env python3 AI-hint: Migrates active session...

!/usr/bin/env python3
AI-hint: Migrates active session state, tool outputs, and context from a large model to a smaller/local model by serializing the A2A-context blackboard and dispatching a TAKE-OVER frame to a target peer or skill.
AI-related: mios-a2a-delegate

<!-- mios-src:28b9411403ac from usr/libexec/mios/mios-handoff:1-3 -->

### !/usr/bin/env python3 AI-hint: Enforcement gate for the...

!/usr/bin/env python3
AI-hint: Enforcement gate for the NO-HARDCODE law (Architectural Law 7). Read-only repo scan that FAILS on three regression classes the law forbids: (1) a literal date/timestamp or dated attribution in COMMENT or DOCSTRING text (timeless-comment rule -- comments must describe the WHY abstractly, not pin code to a frozen history); (2) a dated attribution baked into a .py STRING LITERAL's prose -- a date inside served CSS/JS-comment text or inside a system-prompt string -- which the #-comment scan never sees; and (3) AI-Hint header crash-risks (a UTF-8 BOM not at byte 0, or a header placed above a shebang) that break the install/CI parse. Comment- and value-aware: .py uses tokenize + AST docstrings + a string-token scan that flags only a whitespace-led (prose) date and EXEMPTS a date used as a VALUE -- a quote-led/standalone protocol-version or config literal, or one glued into a URL/slug/identifier token; other files use quote-aware comment detection, so legitimate date CONFIG VALUES are never flagged -- only doc/comment/attribution text. Mirrors the 98-drift-checks fitness-function pattern (exit 1 on violation, MIOS_HARDCODE_LINT_SOFT=1 for advisory). Pairs with the surface-parity + module-boundary gates as the offline LAW gate.
AI-related: ../../../automation/98-drift-checks.sh, ../../share/mios/mios.toml, ./mios-ai-tag, ../../../CLAUDE.md
AI-functions: _date_in_comment_py, _date_in_string_py, _date_in_comment_generic, _header_risk, scan, main

<!-- mios-src:2c8768f5333a from usr/libexec/mios/mios-hardcode-lint:1-4 -->

### !/bin/bash AI-hint: Launches and manages the ChromeDev...

!/bin/bash
AI-hint: Launches and manages the ChromeDev flatpak instance on port 9222, providing a dedicated, isolated profile for the Hermes-Agent to perform CDP-based browser actions like navigation and screenshots.
AI-related: /usr/libexec/mios/mios-hermes-browser, mios-gui, localhost:9222
AI-functions: cdp_responding, wait_for_cdp, kill_existing

<!-- mios-src:72217ad36b3e from usr/libexec/mios/mios-hermes-browser:1-4 -->

### !/bin/bash AI-hint: A shim script that injects a minimal...

!/bin/bash
AI-hint: A shim script that injects a minimal Python stub for the missing `hermes_cli.dashboard_auth` package to prevent `hermes-dashboard.service` from crash-looping due to a broken upstream import in the web_server.py module.
AI-related: /usr/lib/mios/agents/.venv/lib64/python3.14/site-packages, /usr/lib/mios/agents/.venv/bin/python3, hermes-dashboard.service, hermes-agent.service

<!-- mios-src:1fa8c3919de7 from usr/libexec/mios/mios-hermes-dashboard-auth-stub:1-3 -->

### !/usr/bin/env bash AI-hint: Initializes the Hermes gateway...

!/usr/bin/env bash
AI-hint: Initializes the Hermes gateway and web-ui components by generating /etc/mios/hermes/api.env and seeding /var/lib/mios/hermes/config.yaml based on mios.toml configurations during first boot.
AI-related: /etc/mios/hermes/api.env, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-dashboard.sh, /etc/mios/hermes, /etc/mios/hermes/discord.env, /usr/lib/mios/agents/.venv., /usr/lib/mios/agents/.venv/bin/hermes, /usr/lib/mios/hermes-agent/.venv/bin/hermes, /usr/libexec/mios/mios-pc-vision
AI-functions: _log, _mios_toml_value, _sync_discord_env_var, _op_cfg_should_reseed, _soul_should_seed, _build_soul_runtime_ctx, _refresh_soul_ctx, _skill_should_seed, _seed_one_skill, _wait_for_owui

<!-- mios-src:e62cc6f467c2 from usr/libexec/mios/mios-hermes-firstboot:1-4 -->

### !/usr/bin/env python3 AI-hint: Python hook that executes...

!/usr/bin/env python3
AI-hint: Python hook that executes mios-env-probe on the first turn of a session to inject a system environment snapshot into the LLM context, providing the agent with initial awareness of the host environment.
AI-related: /usr/libexec/mios/mios-env-probe, mios-env-probe, mios-apps, mios-launch

<!-- mios-src:3fdc57cab9f5 from usr/libexec/mios/mios-hermes-init-hook:1-3 -->

### !/bin/bash AI-hint: Syncs the core hermes-soul.md identity...

!/bin/bash
AI-hint: Syncs the core hermes-soul.md identity file from system shares to the gateway-service home AND every interactive operator ~/.hermes home, so the agent's persona stays consistent across both the service and the `hermes` CLI REPL while preserving operator-appended runtime context.
AI-related: /usr/libexec/mios/mios-hermes-soul-sync, /usr/share/mios/ai/hermes-soul.md, mios-hermes, hermes-agent.service
AI-functions: log, sync_one

<!-- mios-src:1912e9194cbb from usr/libexec/mios/mios-hermes-soul-sync:1-4 -->

### !/usr/bin/env python3 AI-hint: Tailer script that parses...

!/usr/bin/env python3
AI-hint: Tailer script that parses hermes-agent.service logs to extract tool-calls and sub-agent tasks into /var/lib/mios/hermes-tail/latest.json, providing the real-time status bridge for the OWUI mios_sidecar filter.
AI-related: mios-open-webui, mios-find, hermes-agent.service

<!-- mios-src:cace8df67e70 from usr/libexec/mios/mios-hermes-tail:1-3 -->

### !/bin/sh AI-hint: Wraps host GUI binaries (e.g....

!/bin/sh
AI-hint: Wraps host GUI binaries (e.g., gnome-software) to inject the operator's session environment (DISPLAY, WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS) via systemd-run when invoked by service-level agents.
AI-related: /usr/libexec/mios/mios-host-launch, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-as-operator, mios-as-operator, mios-hermes, mios-hostgui

<!-- mios-src:9e0d7660e105 from usr/libexec/mios/mios-host-launch:1-3 -->

### !/bin/bash AI-hint: A shim script that resolves and opens...

!/bin/bash
AI-hint: A shim script that resolves and opens the mios.html configurator in the operator's default browser via a WSL UNC path, mapping "configurator" and "settings" commands to the UI for editing mios.toml.
AI-related: /usr/libexec/mios/mios-html, /usr/share/mios/configurator/mios.html, /usr/share/mios/configurator/., mios-windows, mios-find

<!-- mios-src:3c664c37ca3d from usr/libexec/mios/mios-html:1-3 -->

### !/usr/bin/env python3 AI-hint: Python script for offline...

!/usr/bin/env python3
AI-hint: Python script for offline ingestion of local files (md, txt, rst, org) into the Postgres+pgvector knowledge table via parameterized mios-pg-query (extended-protocol bound params), utilizing mios-summarize to generate tiered L0/L1 metadata for the viking:// vault.
AI-related: mios-summarize, mios-pg-query

<!-- mios-src:ced746475eea from usr/libexec/mios/mios-ingest:1-3 -->

### !/bin/bash AI-hint: Unified cross-platform package manager...

!/bin/bash
AI-hint: Unified cross-platform package manager entry point that abstracts winget, dnf, and flatpak into a single interface for installing, searching, and listing software across Windows and Linux environments.
AI-related: /usr/libexec/mios/mios-installer, mios-windows, mios-hermes
AI-functions: _dim, _bold, _have_winget, _have_dnf, _have_flatpak, _detect_backend, _search_winget, _search_dnf, _search_flatpak, _install_winget

<!-- mios-src:a42a8d6eecc9 from usr/libexec/mios/mios-installer:1-4 -->

### !/usr/bin/env bash AI-hint: A dual-mode helper that unlocks...

!/usr/bin/env bash
AI-hint: A dual-mode helper that unlocks the gnome-keyring-daemon using credentials from mios.toml, supporting both proactive systemd startup and D-Bus activation to provide libsecret and xdg-desktop-portal Secret support for local and Flatpak apps.
AI-related: /usr/libexec/mios/mios-keyring-autounlock, /etc/mios/mios.toml, /usr/share/mios/mios.toml
AI-functions: _mios_toml_value

<!-- mios-src:ae6721b42208 from usr/libexec/mios/mios-keyring-autounlock:1-4 -->

### !/usr/bin/env python3 AI-hint: CLI for the...

!/usr/bin/env python3
AI-hint: CLI for the PostgreSQL/pgvector Personal Knowledge Graph (PKG) that resolves ambiguous user phrases into concrete app targets via the kg_lookup() helper in the agent-pipe. WS-A3: every read + write is PARAMETERIZED via `mios-db --pg-json` (phrase/target/filter/env/NDJSON values bound out-of-band through mios-pg-query's extended protocol, never f-string-spliced); the hand-rolled _pgq/_pgesc escaping is gone.
AI-related: /usr/libexec/mios/mios-db, mios-db, /usr/libexec/mios/mios-pg-query, mios-apps, args.target

<!-- mios-src:311c23be4aeb from usr/libexec/mios/mios-kg:1-3 -->

### !/usr/bin/env python3 AI-hint: Registers markdown files or...

!/usr/bin/env python3
AI-hint: Registers markdown files or directories into the OWUI `file` table and links them to a named Knowledge collection to enable RAG capabilities for the MiOS-Agent model via meta.knowledge binding.
AI-related: mios-compact, mios-owui-apply-knowledge, mios-agent, mios-cache-clear, mios-owui-bootstrap-admin

<!-- mios-src:9c6979c1cbbb from usr/libexec/mios/mios-knowledge-add:1-3 -->

### !/usr/bin/env python3 AI-hint: Python shim providing...

!/usr/bin/env python3
AI-hint: Python shim providing sub-agents with a tool to query OWUI RAG knowledge collections via the /api/v1/retrieval/process/query endpoint, with a fallback to local pgvector storage if the OWUI API is unreachable.
AI-related: /usr/libexec/mios/mios-pg-query, mios-pg-query, mios-daemon-agent, mios-owui-install-pipe, open-webui (port key `open_webui`), mios-llm-light (port key `llm_light`)
AI-functions: print

<!-- mios-src:316c7b39ca27 from usr/libexec/mios/mios-knowledge-search:1-4 -->

### !/bin/bash AI-hint: Checks and configures Windows-side port...

!/bin/bash
AI-hint: Checks and configures Windows-side port forwarding for MiOS services (e.g., Open WebUI, mios-llm-light) to ensure LAN/Wi-Fi accessibility from the host machine via a PowerShell helper script.
AI-related: /usr/libexec/mios/mios-lan-status, /usr/libexec/mios/Setup-MiOSLanPortProxy.ps1, mios-windows
AI-functions: windows_ip

<!-- mios-src:062f7996177e from usr/libexec/mios/mios-lan-status:1-4 -->

### !/bin/bash AI-hint: Universal launcher that resolves and...

!/bin/bash
AI-hint: Universal launcher that resolves and executes applications across multiple environments (internal services, URLs, Windows binaries, MiOS shims, Linux GUI apps, and PATH binaries) based on a prioritized resolution logic.
AI-related: /usr/libexec/mios/mios-launch, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-open-url, mios-windows, mios-doctor, mios-flatpak-install, mios-skill-clone, mios-tool-clone, mios-gui
AI-functions: _alias_resolve, _pf_flatpak, _broker_dispatch, _launcher_override

<!-- mios-src:0940bbaaab50 from usr/libexec/mios/mios-launch:1-4 -->

### !/usr/bin/env python3 AI-hint: Broker service that provides...

!/usr/bin/env python3
AI-hint: Broker service that provides a Unix socket for the mios-hermes agent to execute shell commands within the operator's user context, enabling GUI apps and Windows .exe interop via the operator's environment.
AI-related: /etc/mios/launcher-token, mios-hermes, mios-launcher, mios-find, mios-wslg, mios-launcher.service, 992.service, hermes-agent.service, socket.socket

<!-- mios-src:a1c3955c2a44 from usr/libexec/mios/mios-launcher-daemon:1-3 -->

### !/bin/bash AI-hint: Linux-side filesystem search shim that...

!/bin/bash
AI-hint: Linux-side filesystem search shim that provides a unified interface for agents to locate files/directories using plocate, locate, or find, supporting filtering by count, extension, type, and specific subtrees.
AI-related: /usr/libexec/mios/mios-locate, mios-everything, mios-locate-err, mios-ai
AI-functions: usage, _filter_results

<!-- mios-src:fdad8c4a95f4 from usr/libexec/mios/mios-locate:1-4 -->

### !/usr/bin/env bash AI-hint: Resolves the DB-driven LOGIN...

!/usr/bin/env bash
AI-hint: Resolves the DB-driven LOGIN account the dashboards advertise -- the globally-controlled account SSOT (pgvector), NOT the operator DISPLAY name ([user].name). Shared by the Linux dashboard and the Windows dashboard (via wsl) so the two never drift. Interim consumer ahead of the full WS-ACCT control plane (task_151..task_154).
AI-related: /usr/libexec/mios/mios-dashboard.sh, powershell/profile.ps1, mios-pg-query, account (pgvector table)
AI-functions: _db_name, main

<!-- mios-src:eb1f21d74488 from usr/libexec/mios/mios-login-account:1-4 -->

### !/usr/bin/env python3 AI-hint: The generative documentation...

!/usr/bin/env python3
AI-hint: The generative documentation CLI. Builds the comment corpus ledger that makes "this comment's knowledge landed in a doc" a machine-checkable fact, and reports the census that drives the documentation ratchet. Only `prune` edits source, and only where the landing predicate proves the knowledge is already in a doc.
AI-related: usr/lib/mios/mios_comments.py, usr/share/mios/reference/manual-corpus.tsv, automation/98-drift-checks.sh, docs/design/doc-generative-documentation.md
AI-functions: cmd_ledger, cmd_audit, cmd_coverage, cmd_harvest, cmd_prune, cmd_landing, cmd_check_functions, cmd_render, check_harvested_functions, landed, main

<!-- mios-src:c84942bfd78e from usr/libexec/mios/mios-manual:1-4 -->

### !/bin/bash AI-hint: A shim script that constructs and opens...

!/bin/bash
AI-hint: A shim script that constructs and opens Google Maps URLs for locations or directions, providing a single-call interface for agents to bypass complex URL construction and browser-launch logic.
AI-related: /usr/libexec/mios/mios-map, mios-open-url
AI-functions: usage, urlencode

<!-- mios-src:27997e542ab3 from usr/libexec/mios/mios-map:1-4 -->

### !/usr/bin/env python3 AI-hint: Provides a Model Context...

!/usr/bin/env python3
AI-hint: Provides a Model Context Protocol (MCP) server that exposes MiOS verbs and resources through the upstream dual-era FOSS SDK.
AI-related: mios-daemon-agent, mios-mcp, mios-mcp.service, agent-pipe (port key `agent_pipe`)

<!-- mios-src:ddcc821acb68 from usr/libexec/mios/mios-mcp-server:1-3 -->

### !/bin/bash AI-hint: A CLI shim that launches a local...

!/bin/bash
AI-hint: A CLI shim that launches a local browser-based markdown editor and live previewer, converting local files or inline strings into a URL-encoded state for the standalone viewer at /usr/share/mios/markdown/index.html.
AI-related: /usr/share/mios/markdown/index.html., /usr/libexec/mios/mios-md, /usr/share/mios/markdown/index.html, mios-html, mios-windows
AI-functions: _urlenc

<!-- mios-src:5e8826ba8b43 from usr/libexec/mios/mios-md:1-4 -->

### !/usr/bin/env python3 AI-hint: Thin client for the resident...

!/usr/bin/env python3
AI-hint: Thin client for the resident qwen3:1.7b model on the mios-llm-light /v1 lane, providing low-latency (<500ms) classification for mios-log-watcher, mios-cron-director, and other system agents.
AI-related: /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-log-watcher, mios-cron-director, mios-log-watcher.service, mios-cron-director.service, localhost:8450

<!-- mios-src:aeff2394fa19 from usr/libexec/mios/mios-micro-llm:1-3 -->

### !/usr/bin/env python3 AI-hint: Acts as the primary...

!/usr/bin/env python3
AI-hint: Acts as the primary OpenAI-compatible entry point and load balancer for MiOS, routing requests to specific hardware lanes (dGPU, iGPU, CPU) based on performance profiles and managing the 17K-token MCP tool surface.
AI-related: mios-orchestrator, mios-fanout, mios-heavy, mios-cpu, mios-igpu, mios-router, localhost:11451, mios-llm-light (port key `llm_light`), mios-llm-heavy/vLLM (port key `vllm`)

<!-- mios-src:235ed5510495 from usr/libexec/mios/mios-model-router:1-3 -->

### !/usr/bin/env python3 AI-hint: FBM CLI. `mios models list`...

!/usr/bin/env python3
AI-hint: FBM CLI. `mios models list` prints the DECLARED set from the layered [ai].firstboot_models SSOT joined against what is on disk (it used to glob the filesystem and never open the TOML at all, so it could not show a declared-but-missing model); `sync` re-runs the first-boot provisioner; `add`/`rm` edit the USER overlay at ~/.config/mios/mios.toml rather than the vendor file, per the vendor<host<user cascade; `cache` pre-seeds from a local directory for air-gapped installs.
AI-related: mios-models-firstboot, mios-models-firstboot.service, /usr/share/mios/mios.toml, /usr/lib/mios/mios_toml.py
AI-functions: _user_toml_path, _declared_models, _on_disk, _fmt_size, do_list, do_sync, do_add, do_rm, do_cache, main

<!-- mios-src:c7a426927069 from usr/libexec/mios/mios-models:1-4 -->

### !/usr/bin/env python3 AI-hint: FBM first-boot large-model...

!/usr/bin/env python3
AI-hint: FBM first-boot large-model provisioner. Reads [ai].firstboot_models from the layered mios.toml, downloads each GGUF with resume, VERIFIES its sha256 (streamed, chunked) and discards the part file on mismatch rather than installing an unverified weight, then writes a sentinel so it never re-runs. Degrades open: every failure path exits 0 so a pull can never block boot.
AI-related: mios-models, mios-models-firstboot.service, /usr/share/mios/mios.toml

<!-- mios-src:47c0ed7c9a3d from usr/libexec/mios/mios-models-firstboot:1-3 -->

### !/usr/bin/env python3 AI-hint: Command-line utility to...

!/usr/bin/env python3
AI-hint: Command-line utility to scaffold new MiOS files from canonical templates, interpolating names, dates, and settings.
AI-related: /usr/share/mios/templates/, /usr/share/mios/mios.toml
AI-functions: main, render_template, get_dest_path, next_ordinal

<!-- mios-src:bc475a8a603f from usr/libexec/mios/mios-new:1-4 -->

### !/bin/bash AI-hint: Resolves and launches a URL in the...

!/bin/bash
AI-hint: Resolves and launches a URL in the MiOS-defined default browser or a specified override by resolving mios.toml entries and dispatching via mios-gui to the operator's WSLg desktop session.
AI-related: /usr/libexec/mios/mios-open-url, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-gui, mios-find, mios-window-active, mios-as-operator, mios-wsl-flatpak-heal
AI-functions: _resolve_browser_chain, _parse_summary, _derive_verify_pattern

<!-- mios-src:faf7982011c4 from usr/libexec/mios/mios-open-url:1-4 -->

### !/usr/bin/env python3 AI-hint: The primary entrypoint for...

!/usr/bin/env python3
AI-hint: The primary entrypoint for MiOS OS-control, providing an OpenAI-compliant tool schema, verb catalog, and skill discovery system derived from mios.toml to allow LLMs to execute system operations without hardcoded logic.
AI-related: /usr/libexec/mios/mios-os-control, /usr/share/mios/skills/, /usr/share/mios/skills., /usr/share/mios/mios.toml, /etc/mios/mios.toml, /usr/share/mios/skills, mios-os-recipe, mios-daemon-agent

<!-- mios-src:615311352ead from usr/libexec/mios/mios-os-control:1-3 -->

### !/usr/bin/env python3 AI-hint: Executes allowlisted...

!/usr/bin/env python3
AI-hint: Executes allowlisted, shell-escaped OS-specific commands defined in mios.toml, handling cross-platform path conversion and security-hardened parameter filtering for MiOS system operations.
AI-related: /usr/libexec/mios/mios-os-recipe, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-os-control

<!-- mios-src:9fd396265f5d from usr/libexec/mios/mios-os-recipe:1-3 -->

### !/usr/bin/env python3 AI-hint: Severity-gated pass/fail...

!/usr/bin/env python3
AI-hint: Severity-gated pass/fail parser for an OpenSCAP results file (ARF or XCCDF results XML), the decision half of the BOOT-02 scan-only build gate. Counts rule-result/result=fail entries whose rule severity is at/above a configured threshold (high>medium>low; "any"=every fail), reading the severity from the rule-result's own @severity when present else the Rule@severity it references. Namespace-agnostic (matches by local tag name) so it works across XCCDF 1.1/1.2 + ARF wrappers. Prints the gating-fail count; exits 1 when >0 (build fails), 0 when clean, 2 on an unparseable/missing report (fail-closed -- it is only ever invoked when [compliance].enabled=true). Pure stdlib.
AI-related: ../../../automation/86-oscap-compliance.sh, ../../share/mios/mios.toml, oscap

<!-- mios-src:ebae66cdce41 from usr/libexec/mios/mios-oscap-gate:1-3 -->

### !/usr/bin/env python3 AI-hint: Registers the authoritative...

!/usr/bin/env python3
AI-hint: Registers the authoritative MiOS knowledge corpus from FHS paths into the Open WebUI database, linking specific files and their content to the MiOS-Agent model row for RAG-enabled context.
AI-related: /usr/share/mios/ai/, /usr/share/mios/docs/, /usr/share/mios/hermes/skills/, /usr/share/mios/cookbooks/, /usr/share/mios/prompts/, /etc/mios/system-prompts/, /usr/share/mios/ai/system.md, /usr/share/mios/ai/INDEX.md, /usr/share/mios/ai/audit-prompt.md, /usr/share/mios/ai/hermes-soul.md

<!-- mios-src:46e3c745b50d from usr/libexec/mios/mios-owui-apply-knowledge:1-3 -->

### !/usr/bin/env python3 AI-hint: Python script that...

!/usr/bin/env python3
AI-hint: Python script that synchronizes the Open WebUI database with the MiOS-managed system prompt for the "MiOS-Agent" model, ensuring the agent's persona and capabilities are correctly injected into the UI.
AI-related: /usr/share/mios/open-webui/system-prompts/mios-agent.md, mios-agent

<!-- mios-src:df1d6206d11f from usr/libexec/mios/mios-owui-apply-system-prompt:1-3 -->

### !/usr/bin/env python3 AI-hint: Configures Open WebUI's...

!/usr/bin/env python3
AI-hint: Configures Open WebUI's web-search feature by updating the webui.db SQLite database to enable search augmentation and point the search engine to the local SearXNG instance on the `searxng` port.
AI-related: mios-searxng, mios-owui-apply-system-prompt

<!-- mios-src:ff234faba0a3 from usr/libexec/mios/mios-owui-apply-websearch:1-3 -->

### !/usr/bin/env python3 AI-hint: Bootstraps the initial Open...

!/usr/bin/env python3
AI-hint: Bootstraps the initial Open WebUI admin account by injecting a user into the SQLite database if empty, resolving credentials from mios.toml and secrets.env to ensure operator access during first-boot.
AI-related: /etc/mios/secrets.env, /etc/mios/owui-admin-password, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-hermes-firstboot, mios-open-webui, port key `open_webui`

<!-- mios-src:d448f0c1d66f from usr/libexec/mios/mios-owui-bootstrap-admin:1-3 -->

### !/usr/bin/env python3 AI-hint: Registers the MiOS Computer...

!/usr/bin/env python3
AI-hint: Registers the MiOS Computer Use tool into webui.db to provide the LLM with direct desktop control, vision grounding, and doc-gen capabilities via typed tool_calls instead of generic shell commands.
AI-related: /usr/share/mios/openwebui/tools/mios_computer_use.py, mios-owui-install-tools, mios-hermes-firstboot, mios-docgen, mios-agent, mios-owui-bootstrap-admin, mios-hermes-firstboot.service

<!-- mios-src:d574891adb24 from usr/libexec/mios/mios-owui-install-computer-use:1-3 -->

### !/bin/bash AI-hint: Registers the MiOS Agent pipe and...

!/bin/bash
AI-hint: Registers the MiOS Agent pipe and anti-meta filters into the Open WebUI database, ensuring the "MiOS AI" model is available in the UI dropdown and automatically configured for the agent's interaction logic.
AI-related: /usr/libexec/mios/mios-owui-install-pipe, /usr/share/mios/owui/pipes/mios_agent_pipe.py, /usr/share/mios/owui/pipes/mios_antimeta_filter.py, /usr/share/mios/mios.toml, /etc/mios/mios.toml, /usr/share/mios/owui/pipes/mios_swarm_council_filter.py, /usr/share/mios/owui/pipes/mios_swarm_delegate_filter.py, /usr/share/mios/owui/pipes/mios_swarm_forcetool_filter.py, mios-owui-pipe-payload, mios-agent

<!-- mios-src:0fa5d8fc699e from usr/libexec/mios/mios-owui-install-pipe:1-3 -->

### !/usr/bin/env python3 AI-hint: Registers the MiOS Verbs...

!/usr/bin/env python3
AI-hint: Registers the MiOS Verbs toolset into the webui.db database to provide the LLM with native typed tool_calls for actions like launch_app and mios_find, bypassing terminal-mediated execution.
AI-related: /usr/share/mios/owui/tools/mios_verbs.py, /usr/share/mios/owui/tools/mios_verbs.py., mios-owui-install-pipe, mios-hermes-firstboot, mios-agent, mios-daemon, mios-open-webui, mios-pgvector, mios-forge, mios-llm-light

<!-- mios-src:f448bc919371 from usr/libexec/mios/mios-owui-install-tools:1-3 -->

### !/usr/bin/env python3 AI-hint: CLI tool for managing...

!/usr/bin/env python3
AI-hint: CLI tool for managing Ed25519 identity keys for MiOS agents, used to provision, rotate, and verify signed passport envelopes for authenticated agent-DB operations like tool calls and skill invocations.
AI-related: mios-daemon, mios-db, mios-skills, mios-kg
AI-functions: additionally

<!-- mios-src:a9870173f7f7 from usr/libexec/mios/mios-passport:1-4 -->

### !/usr/bin/env python3 AI-hint: Provides a Windows-host...

!/usr/bin/env python3
AI-hint: Provides a Windows-host computer-use interface for MiOS-Agent via PowerShell scripts to perform screen capture, mouse/keyboard input, and window management on WSL2-hosted systems.
AI-related: /usr/libexec/mios/mios-pc-control, /usr/share/mios/windows, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-windows, mios-oscontrol-server, mios-launch

<!-- mios-src:741b6d722222 from usr/libexec/mios/mios-pc-control:1-3 -->

### !/usr/bin/env python3 AI-hint: Provides vision-based...

!/usr/bin/env python3
AI-hint: Provides vision-based grounding for the PC-CONTROL agent by processing screenshots and natural language queries via a local VLM to return precise JSON coordinates for UI element interaction.
AI-related: /usr/share/mios/docs/agents/PC-CONTROL-LOCAL.md, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-pc-control, mios-grounding, localhost:8450

<!-- mios-src:38c34680bc88 from usr/libexec/mios/mios-pc-vision:1-3 -->

### !/usr/bin/env python3 AI-hint: A standalone Python-based...

!/usr/bin/env python3
AI-hint: A standalone Python-based PostgreSQL wire-protocol client used by the MiOS agent plane to execute SQL directly over a loopback TCP socket when psql or podman access is restricted. Two modes: legacy simple-Query (raw SQL on argv/stdin -- unchanged) AND `--exec-json` parameterized binding (reads a {"sql","params"} or {"statements":[...]} envelope on stdin and uses the v3 EXTENDED query protocol Parse/Bind/Execute/Sync so values are bound out-of-band -> SQL-injection-safe; this is the WS-A3 CLI-safety foundation the python memory tools call).
AI-related: mios-ai, mios-daemon, mios-db, mios-remember, mios-kg, mios-rag, socket.socket, test_mios_pgwire.py
AI-functions: _cstr, _be16, _be32, _be32s, build_parse, build_bind, build_execute, build_sync, encode_param, parse_envelope, _connect, _rls_owner, _apply_owner_scope, run_simple, run_extended, main

<!-- mios-src:0ca233e8a6e1 from usr/libexec/mios/mios-pg-query:1-4 -->

### !/usr/bin/env bash AI-hint: Boot-time guard that lets the...

!/usr/bin/env bash
AI-hint: Boot-time guard that lets the pgvector image float across PostgreSQL MAJORS without stranding the agent datastore -- detects a PG_VERSION/image-tag major mismatch, logical-dumps the old cluster with the OLD image into the initdb restore slot, stashes (never deletes) the old data dir so the new major initdb's clean, and refuses to touch anything if the dump did not succeed.
AI-related: /usr/share/mios/mios.toml, mios-pgvector.container, mios-pgvector-major-upgrade.service, /usr/lib/tmpfiles.d/mios-pgvector.conf, mios-pgvector-backup.service
AI-functions: current_major, target_major, dump_old_cluster, main

<!-- mios-src:0f00d88120ec from usr/libexec/mios/mios-pgvector-major-upgrade:1-4 -->

### !/usr/bin/env python3 AI-hint: WS-9 out-of-process HITL...

!/usr/bin/env python3
AI-hint: WS-9 out-of-process HITL policy-arbiter SERVICE. A tiny stdlib HTTP service (no deps, loopback) that answers the agent-pipe's HITL arbiter client (_hitl_arbiter_verdict): POST / with {verb,tier,args} -> {allow,reason,rule}, decided by the pure mios_arbiter core over an operator-owned policy (MIOS_ARBITER_DENY / _ALLOW / _BLOCK_TIER from install.env). This is the dangerous-verb gate's SECOND, out-of-process opinion -- changeable/ownable without redeploying the agent-pipe. GET / is a health probe. Default policy is allow-all (no deny/allow-list/block-tier) so it ships as a working no-op until the operator sets a policy. Bound to 127.0.0.1 only.
AI-related: /usr/lib/mios/agent-pipe/mios_arbiter.py, /usr/lib/mios/agent-pipe/server.py, /usr/lib/systemd/system/mios-policy-arbiter.service, /usr/share/mios/mios.toml
AI-functions: _policy, do_POST, do_GET, main, class _Handler

<!-- mios-src:2cd3eed7cd5f from usr/libexec/mios/mios-policy-arbiter:1-4 -->

### !/bin/bash AI-hint: Executes PowerShell scripts on Windows...

!/bin/bash
AI-hint: Executes PowerShell scripts on Windows via pwsh.exe or powershell.exe, providing a first-class `powershell_run` verb for agents to interact with Windows cmdlets, registry, and COM objects with standardized JSON output. The success stream is FLATTENED through Out-String at an explicit width because the broker runs with no console: PowerShell's default formatter sizes every column against a window width of -1 and renders each object as a BLANK LINE, so an object-returning cmdlet otherwise reaches the model as empty output. Knobs live in mios.toml [powershell]; manual ch57.
AI-related: /usr/libexec/mios/mios-powershell, mios-ttyd-powershell, mios-ps-XXXXXX, mios-ps, mios-ps-out-XXXXXX, mios-ps-err-XXXXXX, mios-ttyd-powershell.service, usr/share/mios/mios.toml, /usr/lib/mios/userenv.sh
AI-functions: usage, _cfg_bool, _ps_squote, _win_path, _flatten_prologue, _wrap_call, _truncate

<!-- mios-src:7d43eae012ae from usr/libexec/mios/mios-powershell:1-4 -->

### !/bin/bash AI-hint: Displays a list of all MiOS containers...

!/bin/bash
AI-hint: Displays a list of all MiOS containers by reading the root-owned podman-ps.json snapshot, allowing non-root users to view container status and images without direct podman socket access.
AI-related: /usr/libexec/mios/mios-ps, mios-podman-ps, mios-podman-ps.timer

<!-- mios-src:a65bafa9d118 from usr/libexec/mios/mios-ps:1-3 -->

### !/usr/bin/env python3 AI-hint: Python tool for RAG...

!/usr/bin/env python3
AI-hint: Python tool for RAG retrieval that embeds MiOS documentation into Postgres+pgvector (table mios_rag) via nomic-embed-text to provide context to agents during the agent-pipe enrich stage. WS-A3: the ingest INSERT is PARAMETERIZED via mios-pg-query --exec-json (source/content/emb bound out-of-band, never f-string-spliced); the retired legacy transport is removed.
AI-related: /usr/share/mios/docs, /usr/libexec/mios/mios-pg-query, mios-pg-query, localhost:8450
AI-functions: _embed, _pg, _pg_param, _vec, _chunks, cmd_ingest, cmd_query, main

<!-- mios-src:1ba8db3b07a4 from usr/libexec/mios/mios-rag:1-4 -->

### !/usr/bin/env python3 AI-hint: WS-A17 read-mostly local...

!/usr/bin/env python3
AI-hint: WS-A17 read-mostly local package-registry CLI. `list` prints the materialized package index, `verify` checks the committed registry.json is in sync with the live SSOT (exit 1 on drift, used by the drift gate), `generate` materializes the package tree (ai/v1/packages/<author>/<name>/<version>/mios-pkg.toml + registry.json) from the live catalogs via the pure mios_registry core -- ALL gated behind [ai].package_registry (MIOS_PACKAGE_REGISTRY): when the flag is off, generate/verify are inert no-ops so the feature ships dormant. Standalone (adds the agent-pipe dir to sys.path); no server.py, no DB, no network.
AI-related: /usr/lib/mios/agent-pipe/mios_registry.py, /usr/lib/mios/agent-pipe/mios_manifest.py, /usr/share/mios/mios.toml, /usr/share/mios/ai/v1/packages/registry.json, /automation/lib/generate-packages.sh
AI-functions: _items, _project, _emit_toml, cmd_generate, cmd_list, cmd_verify, main

<!-- mios-src:ecd7d54273ad from usr/libexec/mios/mios-registry:1-4 -->

### !/usr/bin/env python3 AI-hint: Active-memory write...

!/usr/bin/env python3
AI-hint: Active-memory write interface for agents to store/update/delete durable facts in the Postgres+pgvector agent_memory table, scoped global/agent:<name>/conversation:<id>. WS-A3: every write is PARAMETERIZED via `mios-db --pg-json` (values bound out-of-band through mios-pg-query's extended protocol -- never f-string-spliced into SQL); the retired legacy transport is removed.
AI-related: /usr/libexec/mios/mios-db, mios-db, /usr/libexec/mios/mios-pg-query, port key `llm_light`
AI-functions: _pg_json, _embed_vec, main

<!-- mios-src:d1df4ef26f07 from usr/libexec/mios/mios-remember:1-4 -->

### !/usr/bin/env bash AI-hint: Always-latest container image...

!/usr/bin/env bash
AI-hint: Always-latest container image resolver. Reads the sidecar image refs from the mios.toml [image.sidecars] SSOT (never a hand-mirrored list), resolves each to its registry digest, and appends the resolution to the SBOM as build provenance (ADR-0003). Refuses to record a digest it did not actually resolve.
AI-related: /usr/share/mios/mios.toml, /usr/lib/mios/mios_toml.py, /usr/lib/mios/bake/plan.d/, MiOS-SBOM.csv
AI-functions: ssot_refs, resolve_ref, main

<!-- mios-src:cf0751b62fc0 from usr/libexec/mios/mios-resolve-latest:1-4 -->

### !/bin/sh AI-hint: Executes smart restarts for MiOS...

!/bin/sh
AI-hint: Executes smart restarts for MiOS services, handling specific logic for Podman Quadlets (systemctl-based), standard systemd units, and hermes-agent soft restarts to clear in-process skill caches.
AI-related: /usr/libexec/mios/mios-restart, /usr/share/mios/hermes/skills/, /usr/share/mios/hermes/skills, /usr/share/mios/hermes/skills/., /usr/share/mios/ai/hermes-soul.md, mios-open-webui, mios-searxng, mios-forge, mios-forgejo-runner
AI-functions: usage

<!-- mios-src:56f1af877819 from usr/libexec/mios/mios-restart:1-4 -->

### !/bin/bash AI-hint: Executes agent-generated code within a...

!/bin/bash
AI-hint: Executes agent-generated code within a bubblewrap-based userspace sandbox, enforcing filesystem isolation, resource limits (cgroups), network restrictions and (task_210) a SECCOMP syscall filter based on specified security levels. Before task_210 the confined process reported `Seccomp: 0`: the filesystem and the network were jailed while mount, ptrace, keyctl and bpf were still reachable from inside. At level=enforce the filter is now mandatory -- if it cannot be built the run is REFUSED, the same stance this script already took for a missing bwrap, because confining a verb with no filter reads as protection while being none. Manual ch62.
AI-related: /usr/libexec/mios/mios-sandbox-exec, /usr/libexec/mios/mios-seccomp-filter, usr/lib/mios/agent-pipe/mios_pipe/access/seccomp.py, /usr/share/mios/mios.toml, mios-ai
AI-functions: _log, _seccomp_arm, run_bwrap

<!-- mios-src:9ef6f9b9a9dd from usr/libexec/mios/mios-sandbox-exec:1-4 -->

### !/usr/bin/env python3 AI-hint: Executes scheduled research...

!/usr/bin/env python3
AI-hint: Executes scheduled research tasks by processing prompts through the agent-pipe with a bounded research path to prevent resource exhaustion, then reporting results to Discord via mios-discord-send.
AI-related: mios-discord-send, mios-agent

<!-- mios-src:713e6495c472 from usr/libexec/mios/mios-scheduled-research:1-3 -->

### !/bin/bash AI-hint: A bash wrapper for capturing the...

!/bin/bash
AI-hint: A bash wrapper for capturing the primary Windows monitor as a PNG via mios-pc-control, supporting optional --open and --clipboard flags to provide a unified interface for remote screen capture.
AI-related: /usr/libexec/mios/mios-screenshot, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-pc-control, mios-find, mios-open-url, mios-window, mios-windows

<!-- mios-src:6c9b5b6a5708 from usr/libexec/mios/mios-screenshot:1-3 -->

### !/usr/bin/env python3 AI-hint: Emits the compiled seccomp...

!/usr/bin/env python3
AI-hint: Emits the compiled seccomp cBPF program that mios-sandbox-exec hands bwrap on --seccomp FD. Reads the denylist and action from mios.toml [sandbox] through the layered resolver, builds the program with the pure mios_pipe.access.seccomp core, and writes it to --out (or stdout). Exits NON-ZERO on any architecture with no verified syscall table or on a denylist that resolves to nothing, so the caller can refuse to run rather than confining a verb with a filter that denies nothing -- which would read as protection while being none.
AI-related: usr/lib/mios/agent-pipe/mios_pipe/access/seccomp.py, /usr/libexec/mios/mios-sandbox-exec, /usr/share/mios/mios.toml, usr/share/doc/mios/manual/ch62-sandbox-seccomp.md
AI-functions: _cfg, main

<!-- mios-src:9bd5f23b36cd from usr/libexec/mios/mios-seccomp-filter:1-4 -->

### !/usr/bin/env python3 AI-hint: SHELL-01 runner for the...

!/usr/bin/env python3
AI-hint: SHELL-01 runner for the persistent PTY substrate. Drives tmux with the pure protocol in mios_pipe.routing.pty: `exec` sends one nonce-framed command into the chat's session (creating it under the bwrap baseline jail on first use), polls the pane until the marker for THAT nonce appears, and returns ACI-elided output plus the exit code and cwd. `gc` reaps sessions idle past [shell_session].idle_s; `list` and `kill` are the operator verbs. Every decision that can be made without a process lives in the pure module and is tested there; this file is the I/O shell around it. Degrades open: tmux absent, the substrate disabled, or the session cap reached all return a structured error rather than raising.
AI-related: usr/lib/mios/agent-pipe/mios_pipe/routing/pty.py, usr/lib/mios/agent-pipe/mios_pipe/routing/aci.py, usr/share/mios/mios.toml [shell_session], usr/lib/systemd/system/mios-shell-session-gc.service
AI-functions: _cfg, _run, _ensure_session, _session_count, do_exec, do_gc, do_list, do_kill, main

<!-- mios-src:12c0d6a9f512 from usr/libexec/mios/mios-shell-session:1-4 -->

### !/bin/bash AI-hint: Executes a SearXNG image search and...

!/bin/bash
AI-hint: Executes a SearXNG image search and opens the top result's URL in the system's default browser, optionally moving the resulting window to a specified screen position.
AI-related: /usr/libexec/mios/mios-show-image, mios-searxng, mios-open-url, mios-window, mios-window-active, port key `searxng`
AI-functions: usage, pick_img_src

<!-- mios-src:7818d032d606 from usr/libexec/mios/mios-show-image:1-4 -->

### !/usr/bin/env bash AI-hint: Hardens Day-N shutdown loops by...

!/usr/bin/env bash
AI-hint: Hardens Day-N shutdown loops by detecting dirty working tree edits (+1 compilations), presenting a formatted git diff preview, and offering choices to carry-forward, include in Day-N updates/builds, or clean/discard changes.
AI-related: usr/libexec/mios/mios-shutdown, usr/libexec/mios/mios-build-driver, usr/share/mios/mios.toml, automation/98-drift-checks.sh
AI-functions: main, check_git_diff, prompt_user

<!-- mios-src:980135b8b7c5 from usr/libexec/mios/mios-shutdown:1-4 -->

### !/bin/bash AI-hint: Copies system-provided Hermes skills...

!/bin/bash
AI-hint: Copies system-provided Hermes skills from /usr/share/mios/hermes/skills/ to the agent's writable home directory to allow local modification and overriding of system-wide skill definitions.
AI-related: /usr/share/mios/hermes/skills/, /usr/libexec/mios/mios-skill-clone, /usr/share/mios/hermes/skills, mios-hermes, hermes-agent.service
AI-functions: usage

<!-- mios-src:d2c333dca277 from usr/libexec/mios/mios-skill-clone:1-4 -->

### !/usr/bin/env python3 AI-hint: CLI tool for mining...

!/usr/bin/env python3
AI-hint: CLI tool for mining repetitive tool_call sequences into typed-verb DAGs in parameterized Postgres/pgvector, providing a unified skill catalog for mios-agent-pipe, Hermes, and OpenCode to share standardized, parameterized action sequences. All SQL is bound out-of-band via mios-db --pg-json ($1..$n placeholders).
AI-related: /usr/libexec/mios/mios-db, /usr/libexec/mios/mios-pg-query, /usr/share/mios/skills, mios-agent-pipe, mios-db, mios-skill, mios-ai, port key `agent_pipe`

<!-- mios-src:da71bb163ad5 from usr/libexec/mios/mios-skills:1-3 -->

### !/usr/bin/env /usr/lib/mios/agents/.venv/bin/python3...

!/usr/bin/env /usr/lib/mios/agents/.venv/bin/python3
AI-hint: Performs 3-constraint spatial normalization for VLM input image resizing and records output dimensions.
AI-related: /usr/libexec/mios/mios-smart-resize, /usr/libexec/mios/mios-pc-control, /usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py

<!-- mios-src:70ad0098ea0d from usr/libexec/mios/mios-smart-resize:1-3 -->

### !/usr/bin/env bash AI-hint: Initializes SR-IOV Virtual...

!/usr/bin/env bash
AI-hint: Initializes SR-IOV Virtual Functions on Physical Functions (PFs) by parsing /etc/mios/sriov.conf and writing to /sys/bus/pci/devices/ paths during early boot to enable multi-device networking.
AI-related: /usr/lib/mios/paths.sh, /etc/mios/sriov.conf, mios-sriov-init.service, network-pre.target
AI-functions: _log

<!-- mios-src:32599dccbef0 from usr/libexec/mios/mios-sriov-init:1-4 -->

### !/usr/bin/env bash AI-hint: Prints the LIVE, copy-pasteable...

!/usr/bin/env bash
AI-hint: Prints the LIVE, copy-pasteable "SSH from your host into the code-server dev container at the MiOS root tree" command. Single source of truth shared by the Linux dashboard (mios-dashboard.sh) and the Windows dashboard (profile.ps1 via wsl) so the two never drift.
AI-related: /usr/libexec/mios/mios-dashboard.sh, powershell/profile.ps1, mios-agents, code-server
AI-functions: _live_port, main

<!-- mios-src:2a29d7129fec from usr/libexec/mios/mios-ssh-dev-cmd:1-4 -->

### !/bin/bash AI-hint: A wrapper for Valve's SteamCMD...

!/bin/bash
AI-hint: A wrapper for Valve's SteamCMD providing a unified interface for game installation, updates, and status checks via both GUI-based URI dispatching and headless SteamCMD commands for server hosting.
AI-related: /usr/libexec/mios/mios-steamcmd, mios-installer, mios-windows
AI-functions: _steamcmd_path, _ensure_steamcmd, _run_steamcmd, _install_uri, _install_cmd, _uninstall_cmd, _update_cmd, _status_cmd, _info_cmd, _login_cmd, _list_installed, _search_store

<!-- mios-src:9309f0a6f129 from usr/libexec/mios/mios-steamcmd:1-4 -->

### !/bin/bash AI-hint: A developer tool to stress-test the...

!/bin/bash
AI-hint: A developer tool to stress-test the agent-pipe chat endpoint via a Python harness, used to validate system stability, concurrency limits, and latency under load.
AI-related: /usr/libexec/mios/mios-stresstest, /usr/lib/mios/agent-pipe/mios_stress.py, port key `agent_pipe`

<!-- mios-src:32b8507aabda from usr/libexec/mios/mios-stresstest:1-3 -->

### !/usr/bin/env python3 AI-hint: Refreshes OWUI's...

!/usr/bin/env python3
AI-hint: Refreshes OWUI's ui.prompt_suggestions by analyzing MiOS state (kanban, daemon nudges, recent intents) via a refine model to generate 5-28 context-aware starter chips for the operator.
AI-related: random.sh, /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-daemon, localhost:8450, localhost:8000, port key `open_webui`

<!-- mios-src:53e26dba7070 from usr/libexec/mios/mios-suggestion-refresh:1-3 -->

### !/usr/bin/env bash AI-hint: Parses mios.toml to arm...

!/usr/bin/env bash
AI-hint: Parses mios.toml to arm concurrent llama-server instances in swarm mode, enforcing vram_budget_mb limits to prevent OOM on shared GPUs and generating per-worker environment files in /run/mios/swarm/.
AI-related: /usr/share/mios/llamacpp/models, /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-llm-worker, mios-swarm-pack
AI-functions: log, arm_worker

<!-- mios-src:db06aabcf927 from usr/libexec/mios/mios-swarm-pack-firstboot:1-4 -->

### !/usr/bin/env bash AI-hint: Applies the code-server /...

!/usr/bin/env bash
AI-hint: Applies the code-server / war-room workspace (an ext4 replica of the MiOS
AI-related: /usr/share/mios/agents/mios-frontier-sync, /usr/libexec/mios/verify-root.sh
AI-functions: log, die, resolve_src, mutability_gate, build_fileset, do_sync

<!-- mios-src:baa5888f81db from usr/libexec/mios/mios-sync-to-root:1-4 -->

### !/usr/bin/env python3 AI-hint: Provides a shared...

!/usr/bin/env python3
AI-hint: Provides a shared, persistent snapshot of hardware, services, and app inventory in pgvector, allowing agents to query current system state via the `sys_env` table instead of performing live probes.
AI-related: /usr/libexec/mios/mios-sys-env, mios-daemon, mios-os-control, mios-env-probe, mios-apps, mios-shim, localhost:8000

<!-- mios-src:1626586a0921 from usr/libexec/mios/mios-sys-env:1-3 -->

### !/usr/bin/env python3 AI-hint: Provides a single JSON blob...

!/usr/bin/env python3
AI-hint: Provides a single JSON blob of hardware (CPU, GPU, RAM, Disk), service status, and model data (via the mios-llm-light API) to the `system_status` verb to prevent the LLM from hallucinating system metrics.
AI-related: mios-open-webui, mios-delegation-prefilter, mios-hermes-tail, hermes-agent.service, mios-open-webui.service, mios-delegation-prefilter.service, mios-hermes-tail.service, mios-llm-light.service

<!-- mios-src:b3e2d1892b9c from usr/libexec/mios/mios-system-status:1-3 -->

### !/usr/bin/env python3 AI-hint: Provides a unified system...

!/usr/bin/env python3
AI-hint: Provides a unified system inspection tool for agents to query journalctl, process lists, and podman containers by abstracting complex command construction and flag validation into a single interface.
AI-related: mios-system-status, mios-podman-ps, mios-podman-ps.service

<!-- mios-src:32aa94805296 from usr/libexec/mios/mios-sysview:1-3 -->

### !/usr/bin/env python3 AI-hint: Thin shim delegating...

!/usr/bin/env python3
AI-hint: Thin shim delegating template rendering to the mios-new canonical generator, preserving the legacy <kind> <target_filepath> [description] contract.
AI-related: /usr/libexec/mios/mios-new, /usr/share/mios/templates/
AI-functions: main

<!-- mios-src:b126857f5771 from usr/libexec/mios/mios-template-engine:1-4 -->

### !/usr/bin/env python3 AI-hint: Provides a robust...

!/usr/bin/env python3
AI-hint: Provides a robust, filesystem-direct text editing primitive for agents to view, create, and mutate files via atomic str_replace or line-based insertion, bypassing unreliable UI-driven keystroke sequences.
AI-related: mios-passport, mios-skills, mios-everything

<!-- mios-src:a9c077753e0b from usr/libexec/mios/mios-text-edit:1-3 -->

### !/usr/bin/env python3 AI-hint: Thin shell-facing CLI over...

!/usr/bin/env python3
AI-hint: Thin shell-facing CLI over the shared usr/lib/mios/mios_toml.py resolver, so bash scripts + `python3 - <<PY` heredocs stop re-rolling their own awk/regex mios.toml scanners (which mishandle multi-line arrays, [[array.of.tables]], inline tables, and quoting) and instead read the SSOT through the ONE real tomllib-backed layered overlay (vendor<host<user, empty-string rule). `mios-toml-get <section[.sub]> <key> [default]` prints a scalar (default/empty if absent); `--section <a.b>` prints the sub-table as JSON (for [[array]]/catalog reads); `--dump <a.b> k1 k2 ...` prints several keys as `k=v` lines to avoid a python spawn per value on hot paths (e.g. the login-path dashboard). Pairs with mios_toml.py + the 29 already-migrated Python tools; drop-in for the `_mios_toml_value` bash helpers.
AI-related: ../../lib/mios/mios_toml.py, ./mios-sync-theme, ./mios-dashboard.sh, ../../../automation/98-drift-checks.sh
AI-functions: main

<!-- mios-src:452d49cd3b38 from usr/libexec/mios/mios-toml-get:1-4 -->

### !/bin/bash AI-hint: Copies a system-shipped MiOS shim from...

!/bin/bash
AI-hint: Copies a system-shipped MiOS shim from /usr/libexec/mios/ to /usr/local/bin/ to create a mutable version that overrides the default on PATH, allowing agents to iteratively modify and improve existing tools.
AI-related: /usr/libexec/mios/mios-tool-clone, mios-windows, mios-windows-experimental
AI-functions: usage

<!-- mios-src:91ff621945a9 from usr/libexec/mios/mios-tool-clone:1-4 -->

### !/bin/bash AI-hint: Configures and toggles the Tailscale...

!/bin/bash
AI-hint: Configures and toggles the Tailscale HTTPS reverse proxy for the ttyd terminal service on port 7681, enabling secure mobile access via the Tailnet based on the [ttyd].tailnet_expose setting in mios.toml.
AI-related: /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-ttyd-launch, mios-ttyd-bash, mios-ttyd-expose.service, mios-ttyd-bash.service
AI-functions: _toml_get

<!-- mios-src:f9d9873e122a from usr/libexec/mios/mios-ttyd-expose:1-4 -->

### !/bin/bash AI-hint: Parses MIOS_TTYD_* environment...

!/bin/bash
AI-hint: Parses MIOS_TTYD_* environment variables and mios.toml configurations to construct and execute the ttyd web terminal process for either bash or powershell shells on specific ports.
AI-related: /usr/libexec/mios/mios-ttyd-launch, /etc/mios/userenv.sh, /usr/libexec/mios/mios-powershell, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-powershell
AI-functions: _toml_get

<!-- mios-src:e169bcce5ca2 from usr/libexec/mios/mios-ttyd-launch:1-4 -->

### !/usr/bin/env bash AI-hint: One-command offline asset...

!/usr/bin/env bash
AI-hint: One-command offline asset vendor refresh tool. Re-pulls vendored k3s, cursors, fonts, and wheels at LATEST tag resolution.
AI-related: /usr/share/mios/vendored/, /usr/share/mios/vendored/VERSIONS.txt, /usr/share/mios/mios.toml
AI-functions: vendor_refresh_k3s, vendor_refresh_cursors, vendor_refresh_fonts, vendor_refresh_wheels

<!-- mios-src:970badaa0abb from usr/libexec/mios/mios-vendor-refresh:1-4 -->

### !/usr/bin/env python3 AI-hint: Synchronously queries the...

!/usr/bin/env python3
AI-hint: Synchronously queries the mios-daemon-agent to verify if an app actually launched via a live window/process probe and historical failure logs, preventing agents from reporting false successes.
AI-related: mios-daemon-agent, mios-window-active

<!-- mios-src:7a4915b6da1b from usr/libexec/mios/mios-verify-launch:1-3 -->

### !/usr/bin/env python3 AI-hint: NO-HARDCODE-VERSION law...

!/usr/bin/env python3
AI-hint: NO-HARDCODE-VERSION law enforcement (Law 7 / ADR-0003). Scans the source tree for hand-pinned version literals in URLs, pip/npm pins, and @sha256 image digests.
AI-related: ../../../automation/98-drift-checks.sh, ../../share/mios/mios.toml, ../lib/mios_toml.py
AI-functions: main, scan_file, load_allowlist

<!-- mios-src:9e2155e6c489 from usr/libexec/mios/mios-version-lint:1-4 -->

### !/usr/bin/env python3 AI-hint: Provides a tiered, read-only...

!/usr/bin/env python3
AI-hint: Provides a tiered, read-only virtual filesystem (viking://) for agents to navigate local skills, knowledge, and memory via L0 (abstract), L1 (overview), and L2 (raw) levels to manage context window density.
AI-related: /usr/libexec/mios/mios-db, mios-db, /usr/libexec/mios/mios-pg-query, mios-summarize

<!-- mios-src:5498c5a51dd6 from usr/libexec/mios/mios-viking:1-3 -->

### !/usr/bin/env python3 AI-hint: Python backend for the...

!/usr/bin/env python3
AI-hint: Python backend for the web_search verb that queries a local SearXNG instance using concurrent fan-out (RAG-Fusion) to provide agents with real-time, grounded data for facts, weather, and news.
AI-related: mios-daemon, mios-searxng (port key `searxng`), localhost:8450

<!-- mios-src:f0ffd61a1163 from usr/libexec/mios/mios-web-search:1-3 -->

### !/usr/bin/env python3 AI-hint: Wine-free native enumerator...

!/usr/bin/env python3
AI-hint: Wine-free native enumerator of the REAL Windows host's installed apps + games, read straight off the drvfs /mnt mount (NO .exe execution) so the inventory is correct even when WSL-interop is hijacked by a Wine binfmt handler. Covers Steam (appmanifest .acf -> steam://rungameid), Epic (.item JSON -> com.epicgames.launcher URI), GOG, Store/UWP (WindowsApps AppxManifest -> shell:AppsFolder\<PFN>!<AppId>), and Start Menu shortcuts (.lnk -> launched by path). Emits one "<name>|<launch-target>|<category>" line per app; mios-apps consumes it as the Windows scan, and mios-windows launch resolves each target on the real host via the in-session executor.
AI-related: /usr/libexec/mios/mios-apps, /usr/libexec/mios/mios-windows, /usr/libexec/mios/mios-find
AI-functions: _win_root, _scan_steam, _scan_epic, _scan_gog, _scan_uwp, _scan_startmenu, main

<!-- mios-src:4908d1c7c6fa from usr/libexec/mios/mios-win-scan:1-4 -->

### !/bin/bash AI-hint: A title-pattern-driven wrapper for...

!/bin/bash
AI-hint: A title-pattern-driven wrapper for mios-pc-control that allows agents to perform window operations (center, focus, move, resize) using human-readable titles instead of raw handles.
AI-related: /usr/libexec/mios/mios-window, /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-pc-control, mios-windows, mios-oscontrol-server
AI-functions: _resolve_hwnd, _show_window, usage, _exec_post, _exec_get, _json_str, _fp

<!-- mios-src:70f5ad6f3606 from usr/libexec/mios/mios-window:1-4 -->

### !/bin/bash AI-hint: Provides a JSON-formatted status of a...

!/bin/bash
AI-hint: Provides a JSON-formatted status of a specific application's window state, used by MiOS Agents to verify if a process is actually visible and presented to the operator rather than just running in the background.
AI-related: /usr/libexec/mios/mios-window-active, mios-windows

<!-- mios-src:819d481df532 from usr/libexec/mios/mios-window-active:1-3 -->

### !/bin/bash AI-hint: Provides a bridge for the agent to...

!/bin/bash
AI-hint: Provides a bridge for the agent to execute Windows-side commands, launch GUI applications, or run elevated PowerShell scripts via WSL interop or Tailscale SSH from within the WSL2 environment.
AI-related: /usr/libexec/mios/mios-windows, /etc/mios/windows-host, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-autocenter, mios-autocenter, mios-find, mios-oscontrol-server, mios-pc-control, mios-hermes-firstboot
AI-functions: _app_paths_resolve, _start_menu_resolve, _startapps_resolve, _mios_executor_endpoint, _mios_launch_via_executor, _mios_arm_autocenter, usage, do_ssh_ps, _build_uri_center_ps

<!-- mios-src:d129e2d8ba34 from usr/libexec/mios/mios-windows:1-4 -->

### !/bin/bash AI-hint: Wraps winget.exe via WSL interop to...

!/bin/bash
AI-hint: Wraps winget.exe via WSL interop to provide a unified JSON-structured interface for MiOS agents to search, install, upgrade, and manage Windows-side packages from the Linux-side agent stack.
AI-related: /usr/libexec/mios/mios-winget, /usr/share/mios/mios.toml, mios-wg-out-XXXXXX, mios-wg-err-XXXXXX
AI-functions: usage, _find_winget, _emit_json_error, _run_winget, _emit_envelope

<!-- mios-src:94f95bf33af9 from usr/libexec/mios/mios-winget:1-4 -->

### !/bin/bash AI-hint: Ensures the flatpak-portal and...

!/bin/bash
AI-hint: Ensures the flatpak-portal and xdg-desktop-portal services are active and responsive on the user bus to prevent sandbox credential failures in WSL2 environments.
AI-related: /usr/libexec/mios/mios-wsl-flatpak-heal, mios-wsl-flatpak-heal.timer, dbus-broker.service, xdg-desktop-portal.service, xdg-document-portal.service, flatpak-portal.service

<!-- mios-src:82acc025ae75 from usr/libexec/mios/mios-wsl-flatpak-heal:1-3 -->

### !/bin/sh AI-hint: Injects WSLg display, Wayland, and...

!/bin/sh
AI-hint: Injects WSLg display, Wayland, and PulseAudio environment variables into the systemd --user manager and D-Bus activation environment to ensure GUI applications and Flatpaks can reach the WSLg compositor.
AI-related: /usr/libexec/mios/mios-wslg-env-import

<!-- mios-src:1b0a120ee651 from usr/libexec/mios/mios-wslg-env-import:1-3 -->

### !/usr/bin/bash AI-hint: Boot-time blade resolver. Resolves...

!/usr/bin/bash
AI-hint: Boot-time blade resolver. Resolves ONE archetype from a five-tier ladder (explicit karg > /etc/mios/role.conf > vendor [blade].type > hardware demotion > hybrid), materializes its capability markers into /etc/mios/blade.d/, writes /run/mios/blade.env, and set-defaults the role target for every subsequent boot. It starts a target ONLY when the role actually changed -- steady-state boots are fully declarative and the markers alone decide what runs.
AI-related: /usr/lib/mios/paths.sh, /usr/lib/mios/blade.sh, /usr/lib/bootc/kargs.d/05-mios-blade.toml, /etc/mios/role.conf, /etc/mios/blade.d/, usr/libexec/mios/mios-blade, usr/share/mios/mios.toml, usr/share/doc/mios/adr/0016-blade-node-topology.md
AI-functions: log, warn

<!-- mios-src:bd60b9f3c576 from usr/libexec/mios/role-apply:1-4 -->

### !/usr/bin/env python3 AI-hint: MiOS system and...

!/usr/bin/env python3
AI-hint: MiOS system and orchestration module providing seed-db-config capabilities.
AI-related: /usr/share/mios/mios.toml, automation/, mios-find, mios-bootstrap, https://github.com/mios-dev/mios-bootstrap/blob/main/field/autounattend/mios-debloat.json, https://github.com/mios-dev/mios-bootstrap/blob/main/field/autounattend/mios-xbox-features.txt
AI-functions: get_seeded_sections, get_pg_config, main

<!-- mios-src:abd3a2e7a366 from usr/libexec/mios/seed-db-config.py:1-4 -->

### !/usr/bin/env bash AI-hint: Initializes MiOS-specific...

!/usr/bin/env bash
AI-hint: Initializes MiOS-specific SELinux policy modules and persistent booleans from /usr/share/selinux/packages/mios/ during first boot, guarded by a sentinel file to ensure one-time execution.
AI-related: /usr/lib/mios/paths.sh, mios-selinux-init, mios-selinux-init.service
AI-functions: _log

<!-- mios-src:0794e6e13904 from usr/libexec/mios/selinux-init:1-4 -->

### !/usr/bin/env python3 AI-hint: Unit-proves the mios-new...

!/usr/bin/env python3
AI-hint: Unit-proves the mios-new scaffolder allocates ordinals from the directory rather than from a frozen literal (Law 16; ADR-0021).
AI-related: /usr/libexec/mios/mios-new, /usr/share/mios/mios.toml, src/mios-rs/miosd/src/main.rs, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md

<!-- mios-src:7ff6a94b7bb8 from usr/libexec/mios/test_mios_new.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Fixtures for...

!/usr/bin/env python3
AI-hint: Fixtures for reconcile-blade.py -- one per ADR-0017 D5 merge rule, including the one that must REFUSE: a config_kv divergence is an operator decision, never an automatic winner.
AI-related: usr/libexec/mios/reconcile-blade.py, usr/share/doc/mios/adr/0017-blade-workload-mobility.md, usr/share/mios/mios.toml
AI-functions: main

<!-- mios-src:10a53229ab4c from usr/libexec/mios/test_reconcile-blade.py:1-4 -->

### !/usr/bin/env bash AI-hint: Executes pre-sysinit fixups for...

!/usr/bin/env bash
AI-hint: Executes pre-sysinit fixups for WSL2 by marking the root mount as rshared to enable systemd mount namespaces and manually creating /dev/net/tun and /dev/fuse nodes to ensure cockpit and rootless podman functionality.
AI-related: mios-wsl-early, cockpit.service, basic.target
AI-functions: _log

<!-- mios-src:26531f309b2b from usr/libexec/mios/wsl-early:1-4 -->

### !/usr/bin/env bash AI-hint: Executes runtime-specific WSL...

!/usr/bin/env bash
AI-hint: Executes runtime-specific WSL initialization by syncing the hostname from install.env and verifying systemd-linger markers to bridge the gap between build-time configuration and WSL-specific boot behavior.
AI-related: /usr/lib/mios/paths.sh, mios-user, mios-wsl-firstboot
AI-functions: _log

<!-- mios-src:8dd4833c7f9a from usr/libexec/mios/wsl-firstboot:1-4 -->

### !/usr/bin/env bash AI-hint: Boot-time bridge script for...

!/usr/bin/env bash
AI-hint: Boot-time bridge script for WSL2 that enforces /etc/wsl.conf integrity, initializes MiOS runtime directories, maps WSLg X11 sockets, and resolves Podman/WSL2 IP subnet conflicts to prevent network shadowing.
AI-related: mios-wsl-init, mios-network
AI-functions: _log, _repair_podman_subnet

<!-- mios-src:5928d790c24b from usr/libexec/mios/wsl-init:1-4 -->
