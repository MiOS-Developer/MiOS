<!-- AI-hint: Antigravity CLI (AGY) project rules for MiOS development pipelines, CI/CD cycles, and artifacting.
     AI-related: /usr/share/mios/ai/system.md, /etc/mios/ai/system-prompt.md, AGENTS.md, GEMINI.md, usr/share/mios/mios.toml, tools/sync-generated.sh -->
# MiOS Antigravity CLI (AGY) Development & CI/CD Rules

Canonical workspace rules for the Google Antigravity CLI (`agy`) and its subagents operating within MiOS development, CI/CD pipelines, and artifacting cycles.

## 1. Operating Substrate & Architectural Invariants

Every Antigravity agent and subagent operating on this repository operates under the singular system contract:
- **`.git` IS `/` and Dual-Structure**: The repository root is the deployed system root overlay. It is structured two ways at once: it is 100% **Linux FHS Compliant** (`usr/`, `etc/`, `var/`), while simultaneously formatted as an **AI Training Image Shape / Corpus Dataset**. Every file carries machine-readable AI hints/docs, feeding the local self-replication and fine-tuning pipelines.
- **Five Invariants**:
  1. `/var` Persists by Default on bootc/ostree systems.
  2. Bootloader and kernel signing is Unified Kernel Image (`shim -> systemd-boot -> signed UKI`) with kargs baked into the UKI, distinct from MOK out-of-tree module signing.
  3. `venus` VirtIO GPU is graphics/Vulkan transport only; CUDA execution requires whole-device VFIO hardware passthrough.
  4. GPU fractioning (`mdevctl`/SR-IOV) is impossible without host-side PF driver; driver-free host uses whole-device `vfio-pci`.
  5. The Blade owns hardware; the MiOS image is an obfuscated guest. Fleet is 2-6 Blades, each its own AP forming one mesh Wi-Fi and HCI mesh VPN cluster. No hosted node is ever an access point.

## 2. Universal Endpoint Contract (Architectural Law 5)

Every agent and subagent communicates strictly over OpenAI-API-compatible interfaces:
- **Endpoint**: `$MIOS_AI_ENDPOINT`, projected from layered MiOS SSOT. Do not substitute hardcoded ports or vendor endpoints.
- **No Cloud-AI URLs**: Never hardcode vendor-cloud endpoints (`generativelanguage.googleapis.com`, `api.anthropic.com`, etc.).
- **OpenAI Standard Verbs**: `/v1/chat/completions`, `/v1/models`, `/v1/embeddings`, function-calling, and structured outputs only.

## 3. Rust Static Binaries Safety Net & Native Secret Storage

- **Rust Static Binaries**: MiOS runs from a refined, canned codebase compiled to static Rust binaries in `tools/native/`. New generators, verification gates, verb backends, and daemons must be implemented as Rust binaries or hardened templates.
- **Native Keyrings**: Secrets, passphrases, and private tokens must never be written as plaintext literals or environment leaks. They are queried and unlocked through Linux native keyrings (`gnome-keyring`, `dbus`, `secret-tool`, or kernel keyrings).

## 4. CI/CD & Pipeline Cycle Standards

When executing in CI/CD pipeline cycles or automated dev-loops:
1. **Definition of Done First**: Establish explicit acceptance criteria and two-sided verification controls (both positive and negative controls) prior to code modifications.
2. **SSOT Primacy (`usr/share/mios/mios.toml`)**: `mios.toml` is the singular source of truth. Any tunable configuration must be lifted to TOML.
3. **Standing Gate Compliance**: All commits must pass the standing verification gates before push:
   - `src/mios-rs/target/debug/mios-gate phase-registry --root /workspaces/MiOS`
   - `src/mios-rs/target/debug/mios-gate ratchet-direction --root /workspaces/MiOS`
   - `src/mios-rs/target/debug/mios-gate credential-literals --root /workspaces/MiOS`
   - `src/mios-rs/target/debug/mios-gate version-literals-ssot --root /workspaces/MiOS`
   - `src/mios-rs/target/debug/mios-gate signature-policy --root /workspaces/MiOS`
   - `python3 tools/ci-suites.py --check`
4. **Projection Synchronization**: Run `bash ./tools/sync-generated.sh` whenever FHS targets, ports, units, or tools are modified. The git index must be clean with 0 unprojected diffs.
5. **Lossless Merge & Preservation**: Never delete, clobber, or drop code without verifying migration and preservation.

## 5. Universal Harness Neutrality & Native MiOS-MCP + tmux-mcp Everywhere

- **Zero Hardcoded Master/Worker/Monitor Roles**: No agent harness (Antigravity, Codex, Claude Code, OpenCode, Gemini, etc.) is permanently hardcoded as Master, Orchestrator, Worker, or Monitor. Absolutely **ANY AGENT** can be dynamically promoted to Orchestrator or Monitor based on whichever Agent's CLI is invoked.
- **Session Leases**: Each running participant registers its own session, keeps its lease private and refreshes it through receive calls. Acknowledgements certify reading only. Automatic role arbitration and conversation transfer are not implemented; follow [the coordination contract](../COORDINATION.md).
- **Native MiOS-MCP + tmux-mcp Everywhere**: Use the combined server's terminal tools and `mios_tmux_nested_workflow` for worker execution, and `mios_agent_send/receive/ack` for addressed messages. A native CLI head binds to its verified human pane; unbound automation uses private headless sessions. Observation does not consume inboxes.
