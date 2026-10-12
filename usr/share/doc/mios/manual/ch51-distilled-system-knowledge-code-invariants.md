<!-- AI-hint: Chapter 51: Distilled System Knowledge & Code Invariants. Losslessly distilled architectural knowledge, operational invariants and technical comments recovered from historical commits and component refactors. -->

# <a name="51_distilled_system_knowledge"></a>Chapter 51: Distilled System Knowledge & Code Invariants

> Part VII: Build, Test & Upstream Maintenance of the [MiOS manual](../manual.md).

> Path Reference: `/usr/share/doc/mios/manual.md#51_distilled_system_knowledge`

#### Overview

This chapter consolidates losslessly distilled architectural knowledge, operational invariants, and technical comments recovered across historical commits and system component refactors in MiOS.

#### Core Subsystem Knowledge & Invariants

##### 1. FHS Root Overlay & WSL Subsystem Invariants
- **Root Mount Propagation (`/usr/libexec/mios/wsl-early`)**: WSL2 `/init` mounts `/` as private by default, causing systemd unit `mount(NULL, "/", ... MS_SLAVE)` calls to fail with `EOPNOTSUPP` (`status=226/NAMESPACE`). `/wsl-early` explicitly sets `/` to `rshared` prior to `basic.target` so Cockpit and rootless Podman mount namespaces initialize cleanly.
- **Char Device Nodes**: Systemd-udevd is condition-gated off under WSL2 (`/sys` is read-only). `/wsl-early` manually provisions `/dev/net/tun` (c 10:200) and `/dev/fuse` (c 10:229) to enable `slirp4netns` and `fuse-overlayfs` for unprivileged containers.
- **WSL Firstboot & Identity**: `/usr/libexec/mios/wsl-firstboot` reads identity from `/etc/mios/install.env` at boot, populates `/etc/hostname` without logind dependencies, and verifies sysusers/tmpfiles materialization idempotently.
- **WSL Theme Bridge (`/usr/libexec/mios/wsl-theme-bridge.sh`)**: Runs a 15s low-overhead registry poll of Windows `AppsUseLightTheme` to sync GNOME `org.gnome.desktop.interface color-scheme` and GTK Adwaita themes for WSLg GUI apps.

##### 2. Agentic AI Stack & Routing Contracts
- **Orchestration Entrypoint**: The primary front door for all agent tools, CLI invocations, and Open-WebUI is the **Agent-Pipe Orchestrator** on the `agent_pipe` port (`MIOS_AI_ENDPOINT`), utilizing served model `MiOS-Agent`.
- **Hermes Gateway Role**: Hermes on the `hermes` port serves as a tool-execution leaf node invoked by the orchestrator, never a direct public entrypoint for interactive `@` commands (preventing direct bypass of refinement, deterministic routing, and council verification loops).
- **Vector & RAG Storage**: Embedding batches (nomic-embed-text 768-dim) are stored in `pgvector` (`MIOS_PORTS_PGVECTOR=5432`) with HNSW indexing over PostgreSQL.

##### 3. Installer & Consolidation Contracts
- **Installer Monolith & Redirector**: `build-mios.sh` acts as a thin redirector that delegates target selection (`fedora` FHS vs `bootc` image mode) to `installation/mios-install.sh`. `mios-install.sh` houses the Phase-0..4 setup logic, preflight assertions, package resolution, and reboot prompts.

##### 4. Hardening, UKI & Security Invariants
- **ComposeFS Integrity**: `automation/77-composefs-verity.sh` reads `[security].composefs_mode` from `mios.toml` to render `/usr/lib/ostree/prepare-root.conf` (enforcing `verity` or `yes` modes for immutable root filesystems).
- **Law Compliance**: System configurations strictly enforce USR-OVER-ETC (Law 1), NO-MKDIR-IN-VAR (Law 2), BOUND-IMAGES (Law 3), UNIFIED-AI-REDIRECTS (Law 5), UNPRIVILEGED-QUADLETS (Law 6), NO-HARDCODE (Law 7), and SSOT-PROJECTION (Law 8).
