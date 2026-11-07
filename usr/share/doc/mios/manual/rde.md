<!-- AI-hint: Decentralized Remote Development Environment (RDE) platform engine manual: DevPod orchestration, OpenTofu infrastructure provisioning, code-server IDE, zero-AGPL copyleft taint gating, and Blade targeting. -->
<!-- AI-related: usr/share/mios/mios.toml [rde], src/mios-rs/miosd/src/rde.rs, tests/test-rde-engine.py, .devcontainer/devcontainer.json -->

# Decentralized Remote Development Environment (RDE)

## 1. Overview and Architecture

The MiOS Remote Development Environment (`miosd rde`) platform engine provides decentralized, reproducible developer environments across local containers, microVMs, and remote MiOS-Metal Blades. It decouples the developer seat from hardware constraints while strictly adhering to sovereign open-source licensing.

The architecture comprises three core tiers configured via the `[rde]` table in `usr/share/mios/mios.toml`:
- **Orchestrator**: DevPod (MPL-2.0), providing client-only devcontainer orchestration without centralized SaaS servers.
- **Infrastructure Engine**: OpenTofu (MPL-2.0), managing virtual infrastructure with full Terraform CLI compatibility.
- **Frontend IDE**: `code-server` (MIT) or `openvscode-server`, served over authenticated local ports or browser desktops.

## 2. Zero-AGPL & Zero-BSL Copyleft Taint Gating

MiOS enforces strict license gating at compile-time and runtime via `verify_zero_agpl_taint`:
- **AGPL-3.0 Rejection**: Coder and related AGPL-3.0 products are strictly rejected to prevent viral network copyleft contamination of enterprise agent pipelines.
- **BSL 1.1 Rejection**: HashiCorp Terraform is rejected due to its non-FOSS Business Source License. `opentofu` is the singular supported infrastructure provider.
- **Auditing**: `miosd rde audit [--json]` programmatically inspects orchestrator, infrastructure, and IDE licenses, returning non-zero if any copyleft taint is detected.

## 3. CLI Subcommands (`miosd rde`)

- `miosd rde up [workspace] [--provider <name>] [--ide <name>] [--dry-run] [--recreate] [--json]`:
  Resolves configuration from SSOT `[rde]`, audits licenses, generates the devcontainer execution plan, and launches the workspace.
- `miosd rde down [workspace] [--destroy] [--dry-run] [--json]`:
  Stops the running devcontainer instance; with `--destroy`, removes container state and persistent workspace volumes.
- `miosd rde status [workspace] [--json]`:
  Queries active RDE instances, inspecting container health, forwarded ports, and provider bindings.
- `miosd rde audit [--json]`:
  Runs standing license audit across configured RDE components.
- `miosd rde tofu <args...>`:
  Forwards commands directly to the OpenTofu binary (`/usr/local/bin/tofu`) with Terraform compatibility flags.

## 4. Providers & Blade Remote Targeting

RDE supports six execution providers:
- `podman`: Rootless OCI container execution on the local host (default).
- `krun`: Hardware-isolated microVM execution via `libkrun`.
- `blade`: Remote execution dispatched over SSH to a MiOS-Metal Blade (`[rde.blade]` target_host, target_port 8100, user, and Ed25519 identity key).
- `docker`, `ssh`, `kubernetes`: Standard compatibility providers.

`miosd rde` forwards `code_server`, `devpod_agent`, `agent_pipe`, `llm_light` and `pgvector` (8900, 8910, 8700, 8500, 8600 by default). The DevPod agent port is resolved from `[ports].devpod_agent`: `rde status` and `rde up` report the resolved value as `devpod_agent_port`, and an overlay that moves it moves the forward instead of adding a second one. The devcontainer files' `forwardPorts` are a separate projection (`[dotfiles.devcontainer].forward_port_keys`) and do not include the agent port.

### Agent port contract (intentional correction)

This manual used to say that forwarded ports "are synchronized from `[ports]`". `miosd rde` then appended `[ports].devpod_agent` beside the compiled 8910, so the default stayed forwarded next to the selected port, and it truncated an out-of-range value to 16 bits. Both were defects. The contract is now:

- The resolved `[ports].devpod_agent` replaces the previous agent port in the forward list, in the same position. The default 8910 is not forwarded unless it is the selected port. Remove the overlay and the vendor port is forwarded again.
- The tiers merge before `miosd rde` reads the value, so when the host and user tiers both set the port, the user tier wins and the port is forwarded once.
- If the selected port is already forwarded under another key, it appears in the list once.
- The value must be an integer from 1 to 65535. Any other integer (0, 65536, a negative number) stops every `miosd rde` verb with `[ports].devpod_agent = <value> is not a TCP port`. It is never wrapped onto another port.
- The other forwards (`code_server`, `agent_pipe`, `llm_light`, `pgvector`) and the devcontainer `forwardPorts` projection do not change.

`[ports]` is derived from `[ports.categories]` after the merge, and `devpod_agent` is a member of `[ports.categories.devtools]`. To move only the agent port, an overlay pins it there: `[ports.categories.devtools]` with `pinned = { devpod_agent = <port> }`.
