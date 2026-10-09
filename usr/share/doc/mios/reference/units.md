<!-- AI-hint: Derived reference documentation for systemd unit files shipped with MiOS. -->

# MiOS Systemd Units

This document is derived directly from the systemd unit files in the repository.

<!-- MIOS-GEN:units -->
| Unit | Directory | Description |
|---|---|---|
| `hermes-dashboard.service` | `usr/lib/systemd/system` | MiOS' Hermes-Agent Dashboard (kanban board + chat + skills UI) |
| `hermes-worker-firstboot.service` | `usr/lib/systemd/system` | MiOS' Hermes-Worker first-boot config seed (:8643 non-thin worker) |
| `hermes-worker.path` | `usr/lib/systemd/system` | MiOS' watch for the Hermes venv -> (re)start hermes-worker |
| `hermes-worker.service` | `usr/lib/systemd/system` | MiOS' Hermes gateway (native tool loop, port key `hermes`) |
| `k3s.service` | `usr/lib/systemd/system` | Lightweight Kubernetes (K3s) |
| `llama-rpc-server.service` | `usr/lib/systemd/system` | MiOS' llama.cpp RPC Server (Headless Blade Compute Node) |
| `mios-account-sync.service` | `usr/lib/systemd/system` | MiOS' live PostgreSQL-to-OS user account sync daemon |
| `mios-additionalimagestores-perms.path` | `usr/lib/systemd/system` | MiOS': watch additionalimagestores for perm changes; retrigger chmod |
| `mios-additionalimagestores-perms.service` | `usr/lib/systemd/system` | MiOS': enforce world-readable perms on /usr/lib/containers/storage |
| `mios-adguard-firstboot.service` | `usr/lib/systemd/system` | MiOS' AdGuard Home first-boot config generator |
| `mios-adguard.container` | `usr/share/containers/systemd` | MiOS' AdGuard Home (DNS ad/tracker/malware sinkhole + resolver) |
| `mios-agent-pipe.service` | `usr/lib/systemd/system` | MiOS' Agent Pipe (router + refine + critic FastAPI; fronts hermes for every gateway) |
| `mios-agent-pipe.socket` | `usr/lib/systemd/system` | MiOS Agent-Pipe Zero-Downtime Socket Activation |
| `mios-agents.service` | `usr/lib/systemd/system` | MiOS' A2O agents super-container (Claude + agy/Gemini + tmux war room + code-server) |
| `mios-ai-firstboot.service` | `usr/lib/systemd/system` | MiOS' AI first-boot provisioning (agent venv + llama.cpp GGUFs) |
| `mios-ai-firstboot.timer` | `usr/lib/systemd/system` | MiOS' AI first-boot provisioning retry (until the sentinel is written) |
| `mios-ai-legacy-forward.service` | `usr/lib/systemd/system` | MiOS' AI Legacy Port Forwarder (8640 -> 8700) |
| `mios-ai.target` | `usr/lib/systemd/system` | MiOS AI Services Target |
| `mios-aios-refresh.service` | `usr/lib/systemd/system` | MiOS' AIOS refresh -- regenerate SSOT-driven role SYSTEMs + discover the A2A fleet |
| `mios-aios-refresh.timer` | `usr/lib/systemd/system` | Periodic MiOS AIOS refresh (SSOT role SYSTEMs + A2A fleet discovery) |
| `mios-attest.service` | `usr/lib/systemd/system` | MiOS RFC 9334 RATS Remote TPM 2.0 Quote Verifier and Zero-Touch Cluster Onboarding Daemon |
| `mios-backup-pgvector.service` | `usr/lib/systemd/system` | MiOS PostgreSQL+pgvector Automated zstd Snapshot Service |
| `mios-backup-pgvector.timer` | `usr/lib/systemd/system` | Daily and Pre-Upgrade Automated PostgreSQL+pgvector Backup Timer |
| `mios-backup-remote.service` | `usr/lib/systemd/system` | MiOS Remote Delta Snapshot Backup Synchronization Service |
| `mios-backup-remote.timer` | `usr/lib/systemd/system` | Daily Off-Site Remote Delta Snapshot Backup Timer |
| `mios-boot-diag.service` | `usr/lib/systemd/system` | MiOS' Boot Diagnostic -- prints service status to console |
| `mios-bootc-switch.path` | `usr/lib/systemd/system` | MiOS' watch for Forgejo Runner build output -> bootc switch |
| `mios-bootc-switch.service` | `usr/lib/systemd/system` | MiOS' bootc-switch from local build sentinel |
| `mios-bound-images-firstboot.service` | `usr/lib/systemd/system` | First-boot Bound Images Provisioner |
| `mios-cdi-detect.service` | `usr/lib/systemd/system` | MiOS' CDI spec detection (WSL vs bare metal vs VM) |
| `mios-ceph-bootstrap.service` | `usr/lib/systemd/system` | MiOS' Ceph Cluster Bootstrap |
| `mios-ceph-snap.service` | `usr/lib/systemd/system` | MiOS CephFS Subvolume Snapshot Service |
| `mios-ceph-snap.timer` | `usr/lib/systemd/system` | Periodic CephFS Subvolume Snapshot Timer |
| `mios-ceph.container` | `usr/share/containers/systemd` | MiOS' Ceph Monitor (Podman-native) |
| `mios-cephfs-quota.service` | `usr/lib/systemd/system` | MiOS CephFS Tenant Dynamic Quota Enforcement Service |
| `mios-cephfs-quota.timer` | `usr/lib/systemd/system` | Periodic CephFS Tenant Dynamic Quota Enforcement Timer |
| `mios-chrony-ptp.service` | `usr/lib/systemd/system` | MiOS Chrony PTP Drop-in Generator |
| `mios-cockpit-link.container` | `usr/share/containers/systemd` | MiOS' Cockpit web-console discovery shim (Podman Desktop link) |
| `mios-cockpit-link.service` | `usr/lib/systemd/system` | MiOS' Cockpit Link Proxy Service |
| `mios-cockpit-link.socket` | `usr/lib/systemd/system` | MiOS' Cockpit Link Proxy Socket |
| `mios-compute.target` | `usr/lib/systemd/system` | MiOS' Compute Role |
| `mios-computer-use-server.service` | `usr/lib/systemd/user` | MiOS' computer-use server (dual MCP + A2A + executor for this desktop) |
| `mios-container-gc.service` | `usr/lib/systemd/system` | MiOS OCI Container Image Storage LRU Garbage Collection |
| `mios-controller.target` | `usr/lib/systemd/system` | MiOS' Controller Role |
| `mios-copy-build-log.service` | `usr/lib/systemd/system` | MiOS' Build Log Copy Service |
| `mios-cpu-isolate.service` | `usr/lib/systemd/system` | MiOS' CPU Core Isolation Engine |
| `mios-cpu-node.container` | `usr/share/containers/systemd` | MiOS' CPU inference node (always-on / gaming-immune last-resort lane) |
| `mios-cron-director.service` | `usr/lib/systemd/system` | MiOS' cron-director (LLM-gated recurring-task scheduler) |
| `mios-daemon.service` | `usr/lib/systemd/system` | MiOS' consolidated micro-LLM daemon (log classify + refusal detect + cron gate) |
| `mios-dashboard-issue.service` | `usr/lib/systemd/system` | MiOS' dashboard -> /etc/issue.d (pre-login banner) |
| `mios-dashboard-issue.timer` | `usr/lib/systemd/system` | MiOS' dashboard /etc/issue.d refresh timer |
| `mios-desktop.target` | `usr/lib/systemd/system` | MiOS' Desktop Role |
| `mios-doc-distill.service` | `usr/lib/systemd/system` | MiOS' daily comment-to-manual distillation |
| `mios-doc-distill.timer` | `usr/lib/systemd/system` | MiOS' daily documentation distillation schedule |
| `mios-embed-backfill.service` | `usr/lib/systemd/system` | MiOS' Embedding Backfill Worker |
| `mios-embed-backfill.timer` | `usr/lib/systemd/system` | MiOS' Embedding Backfill Timer |
| `mios-endpoint.target` | `usr/lib/systemd/system` | MiOS' Endpoint Role |
| `mios-finetune-serve.service` | `usr/lib/systemd/system` | MiOS' fine-tune serve (base+adapter refiner backend) |
| `mios-firewall-ports.service` | `usr/lib/systemd/system` | MiOS': ensure firewalld has the MiOS service ports open |
| `mios-firewall.service` | `usr/lib/systemd/system` | MiOS': Declarative nftables container micro-segmentation firewall |
| `mios-firstboot.target` | `usr/lib/systemd/system` | MiOS' first-boot provisioning |
| `mios-flatpak-init.service` | `usr/lib/systemd/system` | MiOS' flatpak override policy (system-wide XDG grants) |
| `mios-flatpak-install.service` | `usr/lib/systemd/system` | MiOS' Flatpak First-Boot Installer |
| `mios-forge-firstboot.service` | `usr/lib/systemd/system` | MiOS' Forge first-boot admin-bootstrap (Forgejo) |
| `mios-forge.container` | `usr/share/containers/systemd` | MiOS' Git forge (Forgejo) |
| `mios-forgejo-runner-firstboot.service` | `usr/lib/systemd/system` | MiOS' Forgejo Runner first-boot registration |
| `mios-forgejo-runner.container` | `usr/share/containers/systemd` | MiOS' Forgejo Runner (self-hosted CI for /=git working tree) |
| `mios-freeipa-enroll.service` | `usr/lib/systemd/system` | MiOS' FreeIPA Zero-Touch Enrollment |
| `mios-git-root-init.service` | `usr/lib/systemd/system` | MiOS' first-boot: init / as a git working tree of localhost:3000/<user>/mios.git |
| `mios-gpu-amd.service` | `usr/lib/systemd/system` | MiOS' AMD GPU container plumbing (ROCm/KFD + DRI) |
| `mios-gpu-detect.service` | `usr/lib/systemd/system` | MiOS' GPU Environment Detection |
| `mios-gpu-intel.service` | `usr/lib/systemd/system` | MiOS' Intel/AMD iGPU Container Plumbing (i915/xe/amdgpu) |
| `mios-gpu-nvidia.service` | `usr/lib/systemd/system` | MiOS' NVIDIA GPU container plumbing |
| `mios-gpu-pv-detect.service` | `usr/lib/systemd/system` | MiOS' Hyper-V GPU-PV Guest Detection |
| `mios-gpu-status.service` | `usr/lib/systemd/system` | MiOS' GPU passthrough detection and status |
| `mios-grd-setup.service` | `usr/lib/systemd/system` | MiOS' GNOME Remote Desktop Setup |
| `mios-guacamole.container` | `usr/share/containers/systemd` | MiOS' Apache Guacamole Web |
| `mios-guacd.container` | `usr/share/containers/systemd` | MiOS' Apache Guacamole Daemon |
| `mios-ha-bootstrap.service` | `usr/lib/systemd/system` | MiOS' HA Cluster Bootstrap (Pacemaker/Corosync) |
| `mios-ha-node.target` | `usr/lib/systemd/system` | MiOS' HA Cluster Node Role |
| `mios-hardware-fallback.service` | `usr/lib/systemd/user` | MiOS' Automated Network and Audio Fallback Manager |
| `mios-headless.target` | `usr/lib/systemd/system` | MiOS' Headless Role |
| `mios-headscale-firstboot.service` | `usr/lib/systemd/system` | MiOS' Headscale mesh VPN coordinator first-boot config generator |
| `mios-headscale.container` | `usr/share/containers/systemd` | MiOS' Headscale Mesh VPN Control Plane Coordinator |
| `mios-hermes-browser-worker.service` | `usr/lib/systemd/system` | MiOS' Hermes-Browser-Worker (ChromeDev CDP :9223 for the worker) |
| `mios-hermes-browser.service` | `usr/lib/systemd/system` | MiOS' Hermes-Browser (ChromeDev w/ CDP for Hermes-Agent) |
| `mios-hermes-firstboot.service` | `usr/lib/systemd/system` | MiOS' Hermes-Agent first-boot config + key generation |
| `mios-hermes-tail.service` | `usr/lib/systemd/system` | MiOS' Hermes journal tail (in-flight status -> OWUI emitter) |
| `mios-hybrid.target` | `usr/lib/systemd/system` | MiOS' Hybrid role (desktop + k3s-worker + ceph-osd) |
| `mios-hyperv-enhanced.service` | `usr/lib/systemd/system` | MiOS' Hyper-V Enhanced Session Setup (gnome-remote-desktop) |
| `mios-k3s-init.service` | `usr/lib/systemd/system` | MiOS' K3s Manifest Seeder |
| `mios-k3s-master.target` | `usr/lib/systemd/system` | MiOS' K3s Master Role |
| `mios-k3s-worker.target` | `usr/lib/systemd/system` | MiOS' K3s worker role (agent) |
| `mios-k3s.container` | `usr/share/containers/systemd` | MiOS' K3s Service (Podman-native) |
| `mios-keyring-autounlock.service` | `usr/lib/systemd/user` | MiOS' gnome-keyring auto-unlock with mios.toml password |
| `mios-kvmfr-load.service` | `usr/lib/systemd/system` | Load kvmfr kernel module (Looking Glass shared memory device) |
| `mios-launcher.service` | `usr/lib/systemd/user` | MiOS' launcher broker (operator-side) |
| `mios-ledger-sync.service` | `usr/lib/systemd/system` | MiOS CephFS Transactional Ledger Replication Service |
| `mios-libexec-perms.path` | `usr/lib/systemd/system` | MiOS': watch /usr/libexec/mios for perm changes; retrigger chmod |
| `mios-libexec-perms.service` | `usr/lib/systemd/system` | MiOS': enforce exec perms (go+rX) on /usr/libexec/mios |
| `mios-libvirtd-setup.service` | `usr/lib/systemd/system` | MiOS' first-boot libvirtd wiring |
| `mios-llm-heavy.container` | `usr/share/containers/systemd` | MiOS' heavy lane, vLLM engine (OpenAI /v1, PagedAttention + APC; gated) |
| `mios-llm-light.container` | `usr/share/containers/systemd` | MiOS' LLM-Light (llama.cpp multi-model + KV-paging lane, served via the upstream llama-swap proxy, FOSS) |
| `mios-llm-worker@.container` | `usr/share/containers/systemd` | MiOS' swarm worker %i (single-model llama-server, FOSS) |
| `mios-log-archiver.service` | `usr/lib/systemd/system` | MiOS Structured Parquet Log Archiver and Vector Indexer |
| `mios-log-archiver.timer` | `usr/lib/systemd/system` | Hourly MiOS Journal Log Parquet Compaction Timer |
| `mios-log-streamer.service` | `usr/lib/systemd/system` | MiOS Unified Journald Log Aggregation & pgvector Vector Streamer |
| `mios-luks-enroll.service` | `usr/lib/systemd/system` | Enroll LUKS keys automatically using TPM2 |
| `mios-luks-rotate.service` | `usr/lib/systemd/system` | MiOS LUKS2 Drive Encryption Key Rotation Service |
| `mios-luks-rotate.timer` | `usr/lib/systemd/system` | Monthly Automated LUKS2 Disk Encryption Key Rotation Timer |
| `mios-mcp.service` | `usr/lib/systemd/system` | MiOS' Agent Context Service (MCP) |
| `mios-mdns-mesh.service` | `usr/lib/systemd/system` | MiOS Zero-Configuration mDNS Peer Discovery & WireGuard Peering Daemon |
| `mios-models-firstboot.service` | `usr/lib/systemd/system` | First-boot Large-model Provisioner |
| `mios-mok-enroll.service` | `usr/lib/systemd/system` | MiOS' first-boot MOK enrollment for Secure Boot UKI trust |
| `mios-node.container` | `usr/share/containers/systemd` | MiOS' Distributed Edge Micro-Node Quadlet Service |
| `mios-node.service` | `usr/lib/systemd/system` | MiOS ('My OS') Distributed Edge Micro-Node Runtime Daemon |
| `mios-oci-delta-apply.service` | `usr/lib/systemd/system` | MiOS OCI Delta Apply Service (GAP-5) |
| `mios-open-webui.container` | `usr/share/containers/systemd` | MiOS' Open WebUI |
| `mios-opencode-gateway.service` | `usr/lib/systemd/system` | MiOS' OpenCode /v1 gateway (OpenAI adapter fronting the opencode CLI) |
| `mios-otelcol.container` | `usr/share/containers/systemd` | MiOS' OpenTelemetry Collector and Jaeger Trace Viewer |
| `mios-passport-provision.service` | `usr/lib/systemd/system` | MiOS' Phase C.3 agent passport provisioning (Ed25519 keypairs) |
| `mios-pg-replica.service` | `usr/lib/systemd/system` | MiOS PostgreSQL Hot-Standby Streaming Replication Service |
| `mios-pgvector-backup.service` | `usr/lib/systemd/system` | MiOS' daily pg_dump backup of the unified agent-plane datastore (pgvector) |
| `mios-pgvector-backup.timer` | `usr/lib/systemd/system` | MiOS' daily pgvector datastore backup schedule |
| `mios-pgvector-major-upgrade.service` | `usr/lib/systemd/system` | MiOS' pgvector PostgreSQL-major migration guard |
| `mios-pgvector-optimize.service` | `usr/lib/systemd/system` | MiOS pgvector Database Optimization Service |
| `mios-pgvector-optimize.timer` | `usr/lib/systemd/system` | Weekly Automated pgvector Index & Table Maintenance Timer |
| `mios-pgvector.container` | `usr/share/containers/systemd` | MiOS' PostgreSQL + pgvector (unified agent-plane datastore, FOSS) |
| `mios-piper.container` | `usr/share/containers/systemd` | MiOS' Piper Streaming TTS Engine (piper1-gpl http_server) |
| `mios-podman-gc.service` | `usr/lib/systemd/system` | MiOS' Podman Garbage Collection |
| `mios-podman-gc.timer` | `usr/lib/systemd/system` | Weekly Podman Cleanup |
| `mios-podman-ps.service` | `usr/lib/systemd/system` | MiOS' rootful podman snapshot for the agent-pipe portal/dashboard |
| `mios-podman-ps.timer` | `usr/lib/systemd/system` | MiOS' refresh the podman container snapshot for the dashboard |
| `mios-policy-arbiter.service` | `usr/lib/systemd/system` | MiOS' out-of-process HITL policy arbiter (WS-9) |
| `mios-powerd.service` | `usr/lib/systemd/system` | MiOS' Hardware Power Supply Monitor and Battery-Aware AI Downscaler Daemon |
| `mios-pstore.service` | `usr/lib/systemd/system` | MiOS Persistent Kernel Crash Buffer Extractor |
| `mios-pxe-hub.container` | `usr/share/containers/systemd` | MiOS' PXE Boot Hub |
| `mios-radosgw.container` | `usr/share/containers/systemd` | MiOS' Ceph RADOS Gateway (S3 Object Storage) |
| `mios-registry.service` | `usr/lib/systemd/system` | MiOS Local OCI Distribution Registry (T-510) |
| `mios-role.service` | `usr/lib/systemd/system` | MiOS' System Init & Role Engine |
| `mios-searxng.container` | `usr/share/containers/systemd` | MiOS' SearXNG metasearch (privacy-respecting search proxy) |
| `mios-secret-init.service` | `usr/lib/systemd/system` | MiOS Quadlet Secrets Environment Initialization |
| `mios-self-heal.service` | `usr/lib/systemd/system` | MiOS Autonomous Self-Healing Code Remediation Agent |
| `mios-selinux-init.service` | `usr/lib/systemd/system` | MiOS' SELinux Policy Loader |
| `mios-shell-session-gc.service` | `usr/lib/systemd/system` | Reap idle MiOS shell sessions |
| `mios-shell-session-gc.timer` | `usr/lib/systemd/system` | Periodic reap of idle MiOS shell sessions |
| `mios-skills-miner.service` | `usr/lib/systemd/system` | MiOS' Phase C.2 Sequential Pattern Mining over tool_call history |
| `mios-skills-miner.timer` | `usr/lib/systemd/system` | MiOS' Phase C.2 skill-miner cadence (sequential pattern mining) |
| `mios-sriov-init.service` | `usr/lib/systemd/system` | MiOS' Universal SR-IOV Initialization |
| `mios-suggestion-refresh.service` | `usr/lib/systemd/system` | MiOS' starter-chip refresh (revolving suggestions) |
| `mios-suggestion-refresh.timer` | `usr/lib/systemd/system` | MiOS' starter-chip refresh cadence |
| `mios-swarm-pack-firstboot.service` | `usr/lib/systemd/system` | MiOS' swarm small-model pack arming (gpu_profile=swarm only) |
| `mios-sync-theme.service` | `usr/lib/systemd/system` | MiOS theme bridge -- regenerate /etc/mios/theme from mios.toml [colors] |
| `mios-sys-env-refresh.service` | `usr/lib/systemd/system` | MiOS' refresh the live system/environment cache (sys_env) in pgvector |
| `mios-sys-env-refresh.timer` | `usr/lib/systemd/system` | MiOS' refresh cadence for the sys_env environment cache |
| `mios-thermald.service` | `usr/lib/systemd/system` | MiOS Proactive PID Thermal Daemon and Dynamic Power Cap Modulator |
| `mios-tmpfs-spill.service` | `usr/lib/systemd/system` | MiOS Automated tmpfs Spill-to-NVMe Manager |
| `mios-tmpfs-spill.timer` | `usr/lib/systemd/system` | MiOS Periodic Memory Pressure & tmpfs Spillover Monitor Timer |
| `mios-ttyd-bash.service` | `usr/lib/systemd/system` | MiOS' ttyd -- browser pty bridge (Linux bash session) |
| `mios-ttyd-expose.service` | `usr/lib/systemd/system` | MiOS' mobile terminal tailnet exposure (gated by [ttyd].tailnet_expose) |
| `mios-ttyd-powershell.service` | `usr/lib/systemd/system` | MiOS' ttyd -- browser pty bridge (Windows PowerShell session) |
| `mios-user-snapshot@.service` | `usr/lib/systemd/system` | Hourly CephFS delta snapshot and mesh replication for user %i |
| `mios-user-snapshot@.timer` | `usr/lib/systemd/system` | Hourly CephFS delta snapshot schedule for user %i |
| `mios-userdb-render.service` | `usr/lib/systemd/system` | MiOS' PostgreSQL account systemd userdb drop-in renderer |
| `mios-verify-root.service` | `usr/lib/systemd/system` | MiOS' Root Filesystem Verification |
| `mios-verify.service` | `usr/lib/systemd/system` | MiOS' Cryptographic Integrity Audit (fs-verity) |
| `mios-vfio-setup.service` | `usr/lib/systemd/system` | MiOS VFIO & Looking Glass B6 IVSHMEM Setup |
| `mios-vpn-killswitch.service` | `usr/lib/systemd/system` | MiOS Declarative nftables VPN Kill-Switch and Split-Tunnel Guard |
| `mios-wakeword.service` | `usr/lib/systemd/user` | MiOS Three-Stage Acoustic Wake-Word Daemon |
| `mios-wallpaper.service` | `usr/lib/systemd/user` | MiOS' Living Wallpaper Occlusion Engine Daemon |
| `mios-waydroid-init.service` | `usr/lib/systemd/system` | MiOS' Waydroid Android Initialization |
| `mios-webtools-crawl4ai.container` | `usr/share/containers/systemd` | MiOS' web-tools crawl4ai slim engine (Chrome-CDP primary + camoufox fallback) |
| `mios-webtools-firecrawl-api.container` | `usr/share/containers/systemd` | MiOS' web-tools firecrawl API (v1.0.0, self-host no-auth) |
| `mios-webtools-firecrawl-worker.container` | `usr/share/containers/systemd` | MiOS' web-tools firecrawl worker (Bull queue processor) |
| `mios-webtools-firstboot.service` | `usr/lib/systemd/system` | MiOS' web-tools images build-on-demand firstboot service |
| `mios-webtools-redis.container` | `usr/share/containers/systemd` | MiOS' web-tools redis (firecrawl queue + ratelimit store) |
| `mios-whisper.container` | `usr/share/containers/systemd` | MiOS' Whisper Streaming STT Engine (whisper.cpp) |
| `mios-wol-proxy.service` | `usr/lib/systemd/system` | MiOS Signed Proxy Wake-on-LAN Daemon with SecureON Payload |
| `mios-wsl-early.service` | `usr/lib/systemd/system` | MiOS' WSL2 pre-sysinit fixups (rshared root + /dev/{net/tun,fuse}) |
| `mios-wsl-env-import.service` | `usr/lib/systemd/user` | MiOS': import WSLg env into systemd user-bus + dbus activation |
| `mios-wsl-firstboot.service` | `usr/lib/systemd/system` | MiOS' WSL2 First Boot Initialization |
| `mios-wsl-flatpak-export-sync.path` | `usr/lib/systemd/system` | MiOS' Re-fire flatpak->WSL .desktop sync when flatpak installs/uninstalls land |
| `mios-wsl-flatpak-export-sync.service` | `usr/lib/systemd/system` | MiOS' Sync flatpak .desktop + icon exports into /usr/share so WSL Start Menu picks them up |
| `mios-wsl-flatpak-heal.service` | `usr/lib/systemd/user` | MiOS' WSL2g flatpak portal heal -- restart inactive portals |
| `mios-wsl-flatpak-heal.timer` | `usr/lib/systemd/user` | MiOS' WSL2g flatpak portal heal cadence |
| `mios-wsl-graphical-session.service` | `usr/lib/systemd/user` | MiOS': pull in graphical-session.target on WSLg |
| `mios-wsl-init.service` | `usr/lib/systemd/system` | MiOS' WSL2 Runtime Bridge |
| `mios-wsl-interop-priority.service` | `usr/lib/systemd/system` | MiOS': keep WSL interop primary for Windows .exe (disable WINE binfmt shadowing /mnt/c/*.exe) |
| `mios-wsl-runtime-dir.service` | `usr/lib/systemd/system` | MiOS' WSL2 user-runtime-dir fallback |
| `mios-wsl-theme-bridge.service` | `usr/lib/systemd/user` | MiOS' WSL theme bridge (Windows light/dark -> GNOME color-scheme) |
| `mios-wslg-env.service` | `usr/lib/systemd/user` | Import WSLg display environment into the systemd --user manager |
| `mios-wslg-permissions-fix.service` | `usr/lib/systemd/system` | MiOS': chmod /mnt/wslg/runtime-dir to 0700 so weston accepts it as XDG_RUNTIME_DIR |
| `mios-xdg-userdir-init.service` | `usr/lib/systemd/system` | MiOS XDG User Directories Initialization |
| `miosd.service` | `usr/lib/systemd/system` | MiOS Native System Daemon & Supervisor (Rust miosd) |
| `var-home.mount` | `usr/lib/systemd/system` | CephFS mount for user home directories |
| `var-lib-containers.mount` | `usr/lib/systemd/system` | CephFS mount for Podman container storage |
| `var-lib-machines.mount` | `usr/lib/systemd/system` | Virtual Machine and Container Storage (Compatibility) |
| `var-lib-nfs-rpc_pipefs.mount` | `usr/lib/systemd/system` | RPC Pipe File System |

<!-- derived from tracked unit files (198 unit(s)) -->
<!-- /MIOS-GEN:units -->
