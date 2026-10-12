<!-- AI-hint: A machine-readable and human-readable index of all build-automation scripts in the `automation/` directory, used by agents to locate and identify the numbered Phase-2 sub-phase scripts (and the top-level entry-point/bootstrap scripts) that assemble the MiOS OC
     AI-related: /usr/lib/mios/agents/, /usr/share/mios/llamacpp/models, mios-dropin-fanout, mios-build-driver, mios-llm-light, mios-llm-heavy-alt, mios-llm-heavy, mios-pgvector, mios-firewall-init, mios-role -->

<!--  'MiOS' Artifact | Proprietor: 'MiOS' Project | https://github.com/mios-dev/mios -->
> **Proprietor:** 'MiOS' Project
> **Infrastructure:** Self-Building Infrastructure (Personal Property)
> **License:** Licensed as personal property to 'MiOS' Project
> **Source Reference:** MiOS-Core-v0.3.0
---

#  'MiOS' Scripts Index
> **Generated:** 2026-06-04T23:11:33 (refactored 2026-06-13 for current-state accuracy)
> **Status:** Maintained index — keep in sync with `automation/`

```json:knowledge
{
  "summary": "Index of the MiOS build-automation scripts in automation/ — the numbered Phase-2 sub-phases (run in numeric order by build.sh inside the Containerfile) plus the top-level entry-point/bootstrap/installer scripts.",
  "logic_type": "build-automation",
  "tags": [
    "automation",
    "build-pipeline",
    "index"
  ],
  "version": "0.3.0",
  "last_rag_sync": "2026-06-13"
}
```

## Purpose & place in the whole system

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped Fedora
workstation** (the whole OS is a single container image — boot it, `bootc upgrade`
it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is *also* a **local,
self-replicating, agentic AI operating system**. The same image that ships
GNOME 50/Wayland, NVIDIA + AMD ROCm + Intel iGPU via CDI, KVM/libvirt with VFIO
passthrough, and a k3s + Ceph one-node-cluster path also ships a full local agent
stack behind one OpenAI-compatible endpoint (`MIOS_AI_ENDPOINT`).

The scripts indexed below are **how that image is assembled**. The build pipeline
is the first half of the system lifecycle:

> **build pipeline (these scripts) → OCI image → `bootc` lifecycle on the host.**

The `Containerfile` runs every `automation/[0-9][0-9]-*.sh` in numeric order
(`automation/build.sh` is the runner); the numeric prefix encodes dependency
order, so adding a step means dropping a new `NN-name.sh` next to its peers. These
are the **Phase-2 sub-phases** of the documented build pipeline
(Phase-0 bootstrap → Phase-1 Total Root Merge → **Phase-2 build** →
Phase-3 services/users → Phase-4 reboot). The non-numbered scripts at the bottom
of this index are the entry points, bootstrappers, and installers that *invoke*
the pipeline rather than run inside it.

The same numbered mechanism that installs packages and configures SELinux also
stands up the AI plane — the inference lanes, the agent units, and the
PostgreSQL+pgvector schema are just more numbered steps. So this index is also a
map of how the "agentic AI OS" half of MiOS gets baked in:

- `72-hermes-agent.sh` installs the unified agent plane (agent-pipe + MiOS-Hermes
  + opencode) into the image.
- `38-llamacpp-prep.sh` bakes the GGUF weights for **`mios-llm-light`** (the
  primary `llama.cpp`/`llama-swap` inference + embeddings lane on `:11450`) so it
  serves offline.
- `38-vllm-prep.sh` bakes the **`mios-llm-heavy-alt`** (vLLM) gated heavy-lane
  weights; the SGLang `mios-llm-heavy` lane is likewise gated off by default on
  VRAM grounds.
- `34-render-quadlets.sh` / `48-mios-dropin-fanout.sh` render the Quadlet units
  (including the agent + inference + pgvector containers) from `mios.toml`.

Every contribution to these scripts must obey the six **Architectural Laws** that
make MiOS both immutable and agentic at once:
**1 USR-OVER-ETC · 2 NO-MKDIR-IN-VAR · 3 BOUND-IMAGES · 4 BOOTC-CONTAINER-LINT ·
5 UNIFIED-AI-REDIRECTS · 6 UNPRIVILEGED-QUADLETS.** Laws 1–4 keep the image
deterministic, atomic, and self-contained so bootc can upgrade/roll it back;
Laws 5–6 keep the AI plane unified (one `MIOS_AI_ENDPOINT`) and least-privileged.

> **Audience.** Engineers and agents that need to locate a specific configuration,
> driver, or infrastructure-setup step in the build. This is a directory index, not
> a behavioural spec — read the script header (and its `AI-hint`) for the contract
> a given step provides. Package selection is **never** hard-coded in these
> scripts; it flows from `usr/share/mios/mios.toml` via `automation/lib/packages.sh`.

---

## Numbered build sub-phases (Phase-2, run in order by `build.sh`)

> `01-system-files-overlay.sh` is special-cased: it runs **pre-pipeline** from the
> `Containerfile` (applying the `usr/ etc/ srv/ var/` overlay) and is *skipped* by
> `build.sh`. All other numbered scripts run in lexicographic/numeric order.

### `01-repos.sh`
- **Path:** `automation/01-repos.sh`
- **Description:** 01-repos: Fedora 44 overlay on ucore (enables base repos; excludes `kernel`/`kernel-core` from in-container upgrade).

### `02-kernel.sh`
- **Path:** `automation/02-kernel.sh`
- **Description:** 02-kernel: Kernel extras + development headers.

### `05-enable-external-repos.sh`
- **Path:** `automation/05-enable-external-repos.sh`
- **Description:** Enable external/third-party package repositories used later in the pipeline.

### `01-system-files-overlay.sh`
- **Path:** `automation/01-system-files-overlay.sh`
- **Description:** Apply the repo-root `usr/ etc/ srv/ var/` overlay onto the image. **Runs pre-pipeline from the `Containerfile`; skipped by `build.sh`.**

### `09-fonts.sh`
- **Path:** `automation/09-fonts.sh`
- **Description:** 09-fonts: install Geist (sans + mono) + Symbols-Only Nerd Font.

### `10-gnome.sh`
- **Path:** `automation/10-gnome.sh`
- **Description:** 10-gnome: GNOME 50 desktop — pure build-up (Phosh tablet fallback wired alongside).

### `11-hardware.sh`
- **Path:** `automation/11-hardware.sh`
- **Description:** 11-hardware: GPU drivers (Mesa + AMD ROCm + Intel + NVIDIA) — the foundation that lets both the inference lanes and the passthrough VMs claim hardware via CDI.

### `12-virt.sh`
- **Path:** `automation/12-virt.sh`
- **Description:** 12-virt: Virtualization, containers, orchestration, gaming (KVM/QEMU + libvirt + Podman).

### `36-ceph-k3s.sh`
- **Path:** `automation/36-ceph-k3s.sh`
- **Description:** 13-ceph-k3s: Ceph distributed storage + K3s Kubernetes (the one-node-cluster growth path).

### `34-render-quadlets.sh`
- **Path:** `automation/34-render-quadlets.sh`
- **Description:** Render Quadlet container units from `mios.toml` placeholders (`${MIOS_*}` substitution) — including the agent, inference, and `mios-pgvector` units. Must run before unit start.

### `18-apply-boot-fixes.sh`
- **Path:** `automation/18-apply-boot-fixes.sh`
- **Description:** Systemd execution analysis & WSL2 boot-loop fixes.

### `37-k3s-selinux.sh`
- **Path:** `automation/37-k3s-selinux.sh`
- **Description:** SELinux policy adjustments for the K3s lane.

### `20-fapolicyd-trust.sh`
- **Path:** `automation/20-fapolicyd-trust.sh`
- **Description:** Establish fapolicyd trust entries for the deny-by-default execution policy.

### `20-services.sh`
- **Path:** `automation/20-services.sh`
- **Description:** 20-services: Enable systemd services + bare-metal/VM gating.

### `21-moby-engine.sh`
- **Path:** `automation/21-moby-engine.sh`
- **Description:** Moby/Docker engine compatibility layer (LF-normalized to satisfy shellcheck SC1017).

### `22-freeipa-client.sh`
- **Path:** `automation/22-freeipa-client.sh`
- **Description:** 22-freeipa-client.sh — install FreeIPA/SSSD client + arm zero-touch enrollment.

### `23-uki-render.sh`
- **Path:** `automation/23-uki-render.sh`
- **Description:** Render the Unified Kernel Image (UKI).

### `25-firewall-ports.sh`
- **Path:** `automation/25-firewall-ports.sh`
- **Description:** Open service TCP ports via `firewall-offline-cmd` from environment-derived port variables (Hermes, Open WebUI, code-server, Guacamole, Forge, Cockpit link, AdGuard, PXE).

### `26-gnome-remote-desktop.sh`
- **Path:** `automation/26-gnome-remote-desktop.sh`
- **Description:** Configure GNOME Remote Desktop; pre-emptively disable/mask legacy xrdp services that may bleed in from the base image.

### `30-locale-theme.sh`
- **Path:** `automation/30-locale-theme.sh`
- **Description:** 30-locale-theme: Unified dark theme for every window type.

### `31-user.sh`
- **Path:** `automation/31-user.sh`
- **Description:** 31-user: PAM, user creation, groups, sudoers.

### `32-hostname.sh`
- **Path:** `automation/32-hostname.sh`
- **Description:** 32-hostname: Unique per-instance hostname.

### `33-firewall.sh`
- **Path:** `automation/33-firewall.sh`
- **Description:** 33-firewall: generate the persistent `mios-firewall-init` script that maps resolved env ports (SSH, RDP, K3s, Hermes, Open WebUI, …) into firewalld rules at boot.

### `34-gpu-detect.sh`
- **Path:** `automation/34-gpu-detect.sh`
- **Description:** 34-gpu-detect: Bridge to the GPU detection service — blocks NVIDIA modules in VMs, enables the hardware renderer on bare metal, detects the RTX 50-series VFIO reset bug.

### `34-sshd-port.sh`
- **Path:** `automation/34-sshd-port.sh`
- **Description:** Set the sshd listen port from the resolved env (admin SSH vs. Forge git SSH split).

### `35-gpu-passthrough.sh`
- **Path:** `automation/35-gpu-passthrough.sh`
- **Description:** Stage VFIO-PCI GPU passthrough (kargs + module config) for handing a discrete GPU to a guest VM.

### `35-gpu-pv-shim.sh`
- **Path:** `automation/35-gpu-pv-shim.sh`
- **Description:** GPU paravirtualization shim for guest scenarios.

### `35-init-service.sh`
- **Path:** `automation/35-init-service.sh`
- **Description:** 35-init-service: Bridge to the Unified Role Engine — enables `mios-role.service` and `mios-podman-gc.timer`.

### `36-akmod-guards.sh`
- **Path:** `automation/36-akmod-guards.sh`
- **Description:** Guards around akmods kernel-module builds.

### `36-tools.sh`
- **Path:** `automation/36-tools.sh`
- **Description:** 36-tools: CLI tools and the consolidated `mios` command.

### `37-flatpak-env.sh`
- **Path:** `automation/37-flatpak-env.sh`
- **Description:** 37-flatpak-env: Capture the Flatpak environment for boot-time install.

### `38-selinux.sh`
- **Path:** `automation/38-selinux.sh`
- **Description:** 37-selinux: Build-time SELinux policy fixes (per-rule `.te` modules; new booleans/fcontexts land here).

### `72-hermes-agent.sh`
- **Path:** `automation/72-hermes-agent.sh`
- **Description:** UNIFIED agent-plane install driver — installs MiOS-Hermes + agent-pipe + opencode into `/usr/lib/mios/agents/` (shared Python venv, systemd services, core binaries). This is the step that bakes in the agentic AI OS half.

### `38-llamacpp-prep.sh`
- **Path:** `automation/38-llamacpp-prep.sh`
- **Description:** Bake GGUF weights into `/usr/share/mios/llamacpp/models` (from `MIOS_LLAMACPP_BAKE_MODELS`) so the **`mios-llm-light`** lane (the primary `llama.cpp`/`llama-swap` inference + embeddings server on `:11450`) serves them OFFLINE — air-gapped runtime cannot download. Model map: `usr/share/mios/llamacpp/llama-swap.yaml`.

### `38-oh-my-posh.sh`
- **Path:** `automation/38-oh-my-posh.sh`
- **Description:** 38-oh-my-posh: install the Oh-My-Posh prompt customizer.

### `38-vllm-prep.sh`
- **Path:** `automation/38-vllm-prep.sh`
- **Description:** Bake vLLM weights into the image (from `MIOS_VLLM_BAKE_MODEL`) so the gated **`mios-llm-heavy-alt`** Quadlet serves them OFFLINE. Mirrors `38-llamacpp-prep.sh`: build-time, no air-gapped runtime download.

### `38-vm-gating.sh`
- **Path:** `automation/38-vm-gating.sh`
- **Description:** 38-vm-gating: VM service gating + Hyper-V Enhanced Session.

### `39-desktop-polish.sh`
- **Path:** `automation/39-desktop-polish.sh`
- **Description:** 39-desktop-polish: Desktop entries, Cockpit webapp, MOTD.

### `39-opencode.sh`
- **Path:** `automation/39-opencode.sh`
- **Description:** RETIRED — a no-op shim. The opencode install (binary fetch + `opencode.json` landing) was merged into `72-hermes-agent.sh`. Kept as a stable filename placeholder.

### `40-composefs-verity.sh`
- **Path:** `automation/40-composefs-verity.sh`
- **Description:** 40-composefs-verity.sh — render `/usr/lib/ostree/prepare-root.conf` (composefs + fs-verity posture for the read-only `/usr`).

### `40-flatpak-bake.sh`
- **Path:** `automation/40-flatpak-bake.sh`
- **Description:** 40-flatpak-bake: install operator-selected Flatpaks AT BUILD TIME (from `mios.toml`) so first boot is offline-ready.

### `41-gpu-cdi-toolkits.sh`
- **Path:** `automation/41-gpu-cdi-toolkits.sh`
- **Description:** 41-gpu-cdi-toolkits: install vendor CDI generators (AMD + Intel) so containers see the hardware without manual `--device` flags.

### `48-mios-dropin-fanout.sh`
- **Path:** `automation/48-mios-dropin-fanout.sh`
- **Description:** Fan out MiOS systemd drop-ins / Quadlet overlays across their target units.

### `42-cosign-policy.sh`
- **Path:** `automation/42-cosign-policy.sh`
- **Description:** Install the cosign signature-verification policy for image provenance.

### `43-uupd-installer.sh`
- **Path:** `automation/43-uupd-installer.sh`
- **Description:** 43-uupd-installer.sh — install uupd + greenboot (packages from `mios.toml`).

### `44-podman-machine-compat.sh`
- **Path:** `automation/44-podman-machine-compat.sh`
- **Description:** 44-podman-machine-compat.sh — Podman-machine backend compatibility.

### `45-nvidia-cdi-refresh.sh`
- **Path:** `automation/45-nvidia-cdi-refresh.sh`
- **Description:** 45-nvidia-cdi-refresh.sh — wire up NVIDIA CDI auto-refresh services.

### `46-greenboot.sh`
- **Path:** `automation/46-greenboot.sh`
- **Description:** 46-greenboot.sh — wire greenboot health-check services (package installs via `mios.toml`).

### `47-hardening.sh`
- **Path:** `automation/47-hardening.sh`
- **Description:** 47-hardening.sh — enable hardening services (USBGuard, auditd).

### `49-finalize.sh`
- **Path:** `automation/49-finalize.sh`
- **Description:** 49-finalize.sh — final cleanup, systemd preset application, image linting.

### `50-enable-log-copy-service.sh`
- **Path:** `automation/50-enable-log-copy-service.sh`
- **Description:** Enable the build/boot log-copy service.

### `52-bake-kvmfr.sh`
- **Path:** `automation/52-bake-kvmfr.sh`
- **Description:** 52-bake-kvmfr.sh — compile the Looking Glass `kvmfr` kmod against the ucore-hci kernel.

### `53-bake-lookingglass-client.sh`
- **Path:** `automation/53-bake-lookingglass-client.sh`
- **Description:** 53-bake-lookingglass-client.sh — git clone Looking Glass B7, cmake/make, bake the client.

### `90-generate-sbom.sh`
- **Path:** `automation/90-generate-sbom.sh`
- **Description:** 90-generate-sbom: Generate the CycloneDX Software Bill of Materials (SBOM).

### `91-strip-build-toolchain.sh`
- **Path:** `automation/91-strip-build-toolchain.sh`
- **Description:** 91-strip-build-toolchain: remove the build toolchain from the final image (slim + harden).

### `98-boot-config.sh`
- **Path:** `automation/98-boot-config.sh`
- **Description:** 98-boot-config: Boot console + service configuration.

### `99-cleanup.sh`
- **Path:** `automation/99-cleanup.sh`
- **Description:** 99-cleanup: Final image cleanup (mirrors ucore/cleanup.sh).

### `99-postcheck.sh`
- **Path:** `automation/99-postcheck.sh`
- **Description:** 99-postcheck.sh — build-time validation of technical invariants (enforces the Architectural Laws).

---

## Entry-point, bootstrap & installer scripts (invoke the pipeline)

These are **not** Phase-2 sub-phases — they wrap or trigger the build/install
lifecycle.

### `ai-bootstrap.sh`
- **Path:** `automation/ai-bootstrap.sh`
- **Description:** MiOS AI/manifest bootstrap — regenerates directory manifests and syncs the AI-facing docs/wiki.

### `bcvk-wrapper.sh`
- **Path:** `automation/bcvk-wrapper.sh`
- **Description:** Ephemeral QEMU boot test of the built image.

### `bootstrap.sh`
- **Path:** `automation/bootstrap.sh`
- **Description:** MiOS Public Bootstrap — Linux / WSL2.

### `build.sh`
- **Path:** `automation/build.sh`
- **Description:** Master build runner — parses `mios.toml`, enforces environment constraints, iterates every `automation/[0-9][0-9]-*.sh` in numeric order, and renders the framed ASCII progress UI.

### `build-mios.sh`
- **Path:** `automation/build-mios.sh`
- **Description:** MiOS Fedora Server ignition script.

### `enroll-mok.sh`
- **Path:** `automation/enroll-mok.sh`
- **Description:** Secure Boot MOK enrollment helper (for MOK-signed kernel modules).

### `generate-mok-key.sh`
- **Path:** `automation/generate-mok-key.sh`
- **Description:** One-shot MOK key generator.

### `install-bootstrap.sh`
- **Path:** `automation/install-bootstrap.sh`
- **Description:** Interactive ignition installer (Total Root Merge mode) — Phase-0/1 entry.

### `install-fhs.sh`
- **Path:** `automation/install-fhs.sh`
- **Description:** MiOS system-side installer (FHS overlay path).

### `install.sh`
- **Path:** `automation/install.sh`
- **Description:** MiOS system-side installer (FHS overlay path).

### `overlay-builder.sh`
- **Path:** `automation/overlay-builder.sh`
- **Description:** MiOS-DEV overlay — makes the build-host podman machine look and behave like the deployed system root for local builds.

---

## Helper libraries & support scripts (not run by `build.sh`)

For completeness — these are sourced by the numbered scripts or run by operators
out-of-band; they are not part of the in-order build pipeline.

- **`automation/lib/`** — shared shell helpers sourced by the build scripts:
  `common.sh`, `packages.sh` (the `install_packages*` helpers that read
  `mios.toml` — never call `dnf install` directly), `paths.sh`, `globals.sh`,
  `masking.sh`, `agreements-banner.sh`, `ws7-uki-fapolicyd-build.sh`.
- **`automation/support/`** — operator/Day-0/Day-2 support and bring-up scripts
  (e.g. `bringup-pgvector.sh`, `bringup-llama-swap.sh`, `deploy-agent-pipe.sh`,
  `heal-all-services.sh`, `reindex-knowledge.sh`, `smoke-mcp-server.sh`). These
  bring up or repair the running AI/data plane (PostgreSQL+pgvector,
  `mios-llm-light`, the agent-pipe) on a live host; they are not baked-in build
  steps.

---

> **Note (2026-06-13):** `37-ollama-prep.sh` was removed when Ollama was retired.
> Local inference and embeddings now run on the **`mios-llm-light`** lane
> (`:11450`, prepared by `38-llamacpp-prep.sh`); the unified agent datastore is
> **PostgreSQL + pgvector** (`mios-pgvector`), not SurrealDB/Qdrant. Ollama
> survives only as an upstream API-compat reference (the lanes speak the
> OpenAI/Ollama-compatible API). `llama-swap` (`ghcr.io/mostlygeek/llama-swap`)
> remains a legitimate upstream proxy image.

---
- **Copyright:** (c) 2026 'MiOS' Project
- **Status:** Personal Property / Private Infrastructure
- **Project Repository:** [MiOS-DEV/mios](https://github.com/mios-dev/mios)
- **Documentation:** ['MiOS' Navigation Hub](https://github.com/mios-dev/mios/blob/main/specs/Home.md)
- **Artifact Hub:** [ai-context.json](https://github.com/mios-dev/mios/blob/main/ai-context.json)
---
