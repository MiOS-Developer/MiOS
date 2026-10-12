<!-- AI-hint: Comprehensive architectural reference for MiOS as a whole system -- an immutable bootc/OCI Fedora workstation that is also a local, self-replicating agentic AI OS. Maps the build pipeline, repository topology, supply-chain artifacts, inference lanes (mios-llm-light/-heavy), the agent stack (agent-pipe -> Hermes -> pgvector memory -> MCP/A2A), and the operational laws to specific file paths for engineering and automation tasks.
     AI-related: 99-postcheck.sh, /usr/lib/mios/env.d/flatpaks.env, /etc/mios/hermes/api.env, /usr/share/mios/mios.toml, /usr/share/mios/llamacpp/mios-llm-light.yaml, /usr/share/mios/postgres/schema-init.sql, /usr/share/mios/ai/v1/agents.json, /usr/share/mios/ai/system.md, /usr/share/mios/ai/hermes-soul.md, /usr/share/mios/ai/hermes-soul-full.md, /etc/mios/ai/system-prompt.md, /usr/share/mios/ai/v1/mcp.json -->
# 'MiOS' Engineering Reference

## Purpose and audience

This is the engineering-level map of 'MiOS' as a **complete system**: an
immutable, bootc/OCI-shaped Fedora workstation that is *also* a local,
self-replicating, agentic AI operating system. The same image that ships
GNOME/Wayland, NVIDIA+ROCm+Intel-iGPU via CDI, KVM/libvirt with VFIO
passthrough, and a k3s+Ceph one-node-cluster path also ships a full local
agent stack behind one OpenAI-compatible endpoint.

That dual nature shapes every section below. The throughline is:

> **build pipeline -> OCI image -> bootc lifecycle** (the immutable-OS half),
> and **inference lanes -> agent-pipe/Hermes orchestration -> pgvector memory
> -> MCP/A2A** (the agentic-OS half),

with the **repo root being the deployed system root** and the six Architectural
Laws being the contract that lets both halves coexist. Every claim cites a real
file path; this document is written for engineers extending the image and for
automation that needs to reason about where things live.

For the conceptual layout see
[`usr/share/doc/mios/concepts/architecture.md`](../concepts/architecture.md);
for the build-pipeline rules see
[`usr/share/doc/mios/guides/engineering.md`](../guides/engineering.md); for the
agent-facing API contract see [`api.md`](api.md).

---

## §0. Project identity

- Org: **`mios-dev`** (https://github.com/mios-dev). All earlier names --
  *CloudWS-bootc*, *CloudWS-OS* -- are retired; every `cloudws-*` artifact was
  renamed to `mios-*` (see Appendix B).
- Two repos:
  - **System layer:** `https://github.com/mios-dev/MiOS.git` -- the bootc
    image source. Repo root *is* the deployed system root.
  - **Bootstrap/installer:** `https://github.com/mios-dev/mios-bootstrap.git` --
    Phase 0/1/4 of the global install pipeline (preflight, total root merge,
    reboot prompt).
- Published image: **`ghcr.io/mios-dev/mios:latest`**.
- Base image: **`ghcr.io/ublue-os/ucore-hci:stable-nvidia`** (overridable via
  `MIOS_BASE_IMAGE`).
- Image lifecycle: bootc-managed (`bootc switch` / `bootc upgrade` /
  `bootc rollback`) -- upgrade like a `git pull`, roll back like a Ctrl-Z.
- Target hosts: AI workstations, hyperconverged-infra single-nodes, KVM
  passthrough rigs. Not a general-purpose desktop.

The naming convention is global: every shipped artifact is `mios-<component>`
(lowercase-kebab). The proper-noun spelling **`'MiOS'`** (single-quoted) is the
legal-mark form for display strings; lowercase `mios` is the technical
identifier used in paths, env vars, package names, and code.

---

## §1. Repository topology

### `'MiOS'` repo (system layer) -- repo root is system root

```
.                                  # = / on a deployed host
├── automation/                    # Phase-2 build pipeline (~48 NN-prefix scripts)
│   ├── build.sh                   # Master orchestrator (called by Containerfile)
│   ├── lib/
│   │   ├── common.sh              # log/warn/die helpers, dnf flags, version manifest
│   │   ├── packages.sh            # mios.toml [packages.*] parser
│   │   ├── masking.sh             # log secret-mask filter
│   │   └── paths.sh               # build-time MIOS_*_DIR constants
│   ├── 01-repos.sh ... 99-postcheck.sh   # Phase scripts (numeric-ordered)
│   ├── 34-render-quadlets.sh      # Renders ${MIOS_*} placeholders in Quadlet units
│   ├── ai-bootstrap.sh            # AI manifest / Wiki / KB regeneration
│   ├── bcvk-wrapper.sh            # bootc-image-builder convenience wrapper
│   ├── bootstrap.sh               # Local-dev bootstrap helper
│   ├── enroll-mok.sh              # MOK key enrollment
│   ├── generate-mok-key.sh        # One-shot MOK key generator
│   ├── install.sh                 # System-side installer (FHS overlay path)
│   ├── install-bootstrap.sh       # Interactive ignition installer
│   └── manifest.json              # Auto-generated phase-script index
├── usr/                           # → /usr (read-only composefs)
│   ├── lib/
│   │   ├── bootc/
│   │   │   ├── kargs.d/           # Kernel arg fragments (TOML, flat array form)
│   │   │   └── bound-images.d/    # Quadlet image binders (LAW 3)
│   │   ├── mios/                  # 'MiOS' runtime libs (paths.sh, logs/, agent-pipe/, agents/)
│   │   ├── modprobe.d/            # Kernel module overrides
│   │   ├── modules-load.d/        # Module auto-load list (mios.conf)
│   │   ├── profile.d/             # MOTD + WSLg env exports
│   │   ├── sysctl.d/              # Kernel runtime tunables (90-* le9uo, 99-* hardening)
│   │   ├── sysusers.d/            # Static user/group defs (10-mios.conf is canonical)
│   │   ├── systemd/system/        # MiOS-owned units + drop-ins for stock units
│   │   ├── systemd/system-preset/ # 90-mios.preset (enable/disable defaults)
│   │   ├── tmpfiles.d/            # /var/* and /run/* tmpfiles entries (LAW 2)
│   │   └── udev/rules.d/          # Custom udev rules (99-mios-gpu, 99-kvmfr)
│   ├── libexec/
│   │   ├── mios/                  # Private exec dir (motd, role-apply, gpu-detect, mios-build-driver, etc.)
│   │   ├── mios-grd-setup         # GNOME Remote Desktop firstboot setup
│   │   └── mios-flatpak-install   # Flatpak first-boot installer
│   ├── share/
│   │   ├── containers/systemd/    # System-level Quadlet definitions (AI lanes, infra sidecars)
│   │   ├── doc/mios/              # Subsystem docs (concepts/, guides/, reference/, audits/, upstream/)
│   │   ├── mios/
│   │   │   ├── mios.toml          # SSOT: packages, ports, AI lanes, services, agent behaviour
│   │   │   ├── env.defaults       # Vendor environment defaults
│   │   │   ├── mios.toml.example  # Vendor template for ~/.config/mios/mios.toml
│   │   │   ├── configurator/      # Browser-local TOML editor (index.html)
│   │   │   ├── llamacpp/          # mios-llm-light.yaml model map + GGUF model store
│   │   │   ├── postgres/          # schema-init.sql (pgvector agent-DB schema)
│   │   │   ├── kb/manifest.json   # KB delivery index (FHS-compliant location)
│   │   │   └── ai/                # AI surface (system.md, hermes-soul*.md, v1/, etc.)
│   │   └── selinux/packages/mios/ # Custom SELinux .te modules
│   └── lib/extensions/source/     # systemd-sysext source materials
├── etc/                           # → /etc (3-way merge on bootc upgrade)
│   ├── containers/                # containers.conf.d / storage.conf overrides
│   ├── mios/
│   │   ├── eval-criteria.json     # OpenAI Evals grader rubric
│   │   ├── kb.conf.toml           # KB-wide config
│   │   ├── system-prompts/        # Engineer/Reviewer/Troubleshoot prompts
│   │   └── ai/                    # Host-local AI overrides
│   ├── profile.d/                 # mios-motd.sh + mios-wsl2.sh login hooks
│   ├── pki/mios/                  # MOK DER cert (public key)
│   └── wsl.conf                   # WSL2 config (byte-identical to /usr/lib/wsl.conf)
├── srv/mios/                      # Data served by the system
│   └── api/                       # Sample OpenAI v1 API payloads
├── var/                           # Mostly tmpfiles-declared placeholders
│   └── lib/mios/
│       ├── embeddings/            # RAG: chunks.jsonl, vector_store.import.jsonl, ingest_local.py
│       ├── training/              # Fine-tune datasets (sft.jsonl, dpo.jsonl)
│       ├── llamacpp/              # mios-llm-light KV slot-save dir (per-conversation KV paging)
│       ├── pgvector/              # PostgreSQL + pgvector PGDATA (agent datastore)
│       └── evals/                 # OpenAI Evals API artifacts
├── usr/share/mios/prompts/              # XML-structured prompt templates
├── tools/                         # Repo-internal dev/operator tooling
│   ├── lib/                       # Shared helpers (path-refactor.py, ascii-sweep.py,
│   │                              #   quote-mios.py, install-env.ps1, userenv.sh,
│   │                              #   generate-sbom.py)
│   ├── *.sh / *.py / *.ps1        # Operator-runnable scripts (preflight, vfio,
│   │                              #   profilers, etc.)
│   └── windows/                   # Windows-specific helpers
├── config/
│   ├── artifacts/                 # BIB recipes (bib.toml, iso.toml, wsl2.toml);
│   │                              #   qcow2/vhdx rendered by miosd artifact-build
│   └── bootstrap/bootstrap.ps1    # Windows bootstrap PS1
├── Containerfile                  # OCI build entry (single-stage + ctx scratch)
├── Justfile                       # Linux build orchestrator
├── mios-build-local.ps1           # Windows build orchestrator
├── install.ps1                    # Unified Windows installer (build + WSL deploy)
├── Get-MiOS.ps1                   # Bootstrap-from-irm-iex entry point
├── preflight.ps1 / preflight.sh   # Prerequisite checks
├── push-to-github.ps1             # CI helper
├── *.md                           # README, CLAUDE, GEMINI, AGENTS, MiOS,
│                                  #   CONTRIBUTING, AGREEMENTS, llms.txt, ...
└── VERSION                        # Single line: "v0.3.0"
```

Most operator-tunable surface (packages, ports, AI lanes, services, agent
behaviour) now lives in a single SSOT -- `usr/share/mios/mios.toml`, parsed by
`automation/lib/packages.sh` and edited via the configurator HTML at
`/usr/share/mios/configurator/`. Human-readable package rationale lives at
`usr/share/doc/mios/reference/PACKAGES.md` (documentation, not the runtime SSOT).

### `mios-bootstrap` repo (installer layer) -- sibling root overlay

```
.
├── bootstrap.sh / bootstrap.ps1   # Phase-0 entry (preflight + identity capture)
├── install.sh / install.ps1       # Phase-1 total root merge + Phase-3 user create
├── identity.env.example           # Template for the identity envelope
├── image-versions.yml             # Renovate-managed version pins
├── etc/mios/profile.toml          # Host vendor profile
├── etc/skel/.config/mios/         # Per-user seed files
├── usr/share/mios/ai/             # Bootstrap AI seed (system.md, v1/)
├── usr/share/mios/user-preferences.md  # JSON-embedded user-preferences card
├── profile/                       # Profile staging area
└── *.md                           # README, AI, IMPLEMENTATION-SUMMARY,
                                   #   USER-SPACE-GUIDE, VARIABLES, system-prompt
```

---

## §2. Base image and supply chain

Supply-chain integrity is what lets MiOS be reproduced *exactly* on every host
that pulls the ref -- the foundation of both the immutable-OS promise and the
"trust the baked-in agent stack" promise.

### Primary base
- **`ghcr.io/ublue-os/ucore-hci:stable-nvidia`** (Containerfile -- `ARG BASE_IMAGE`).
- Resolved digest captured per build by `automation/build.sh` via `record_version`
  (`automation/lib/common.sh`).

### Alternate bases (build-arg selectable)
- `ghcr.io/ublue-os/ucore-hci:stable` (no NVIDIA).
- `ghcr.io/ublue-os/ucore:stable` (minimal uCore, no HCI extras).

### Renovate
- `renovate.json` at repo root; tracks Containerfile FROM lines, Quadlet
  `Image=` refs, and image-versions.yml entries.

### External OCI images (Quadlet sidecars)
Every `Image=` ref is a pinned upstream reference resolved at build time by
`automation/34-render-quadlets.sh` from `mios.toml [image.sidecars]`.

| Image (upstream) | Quadlet (MiOS unit) |
|---|---|
| `quay.io/ceph/ceph:v19` | `usr/share/containers/systemd/mios-ceph.container` |
| `docker.io/rancher/k3s:<pinned>` | `usr/share/containers/systemd/mios-k3s.container` |
| `ghcr.io/mostlygeek/llama-swap:cuda` | `usr/share/containers/systemd/mios-llm-light.container` |
| `docker.io/vllm/vllm-openai:latest` | `usr/share/containers/systemd/mios-llm-heavy.container` |
| `docker.io/pgvector/pgvector:pg17` | `usr/share/containers/systemd/mios-pgvector.container` |
| `docker.io/crowdsecurity/crowdsec:latest` | `usr/share/containers/systemd/mios-crowdsec-dashboard.container` |
| `docker.io/guacamole/guacamole:latest` | `usr/share/containers/systemd/mios-guacamole.container` |
| `docker.io/guacamole/guacd:latest` | `usr/share/containers/systemd/mios-guacd.container` |
| `docker.io/library/postgres:latest` | `usr/share/containers/systemd/mios-guacamole-postgres.container` |
| `quay.io/poseidon/matchbox:latest` | `usr/share/containers/systemd/mios-pxe-hub.container` |

> Note: `mios-llm-light` (the upstream proxy image `ghcr.io/mostlygeek/llama-swap`)
> and the OpenAI/Ollama-compatible API are legitimate **upstream** references --
> they are kept. Only the MiOS *unit/service identity* is renamed (e.g. the
> llama.cpp lane is `mios-llm-light`, not an Ollama unit). The early
> Ollama/the legacy datastore/Qdrant stack is **removed** (see Appendix B).

### Build-time tools
| Image | Purpose | Where |
|---|---|---|
| `quay.io/centos-bootc/bootc-image-builder:latest` | RAW/ISO/QCOW2/VHDX/WSL2 disk images | `MIOS_BIB_IMAGE` in `Justfile` + `config/artifacts/*.toml` |
| `quay.io/centos-bootc/centos-bootc:stream10` | rechunk fallback | `MIOS_IMG_RECHUNK` in `mios-build-local.ps1` |
| `anchore/syft:latest` | CycloneDX/SPDX SBOM | `automation/90-generate-sbom.sh` + `Justfile` `sbom` target |
| `docker.io/library/alpine:latest` | helper-image fallback | `mios-build-local.ps1` |

### Cosign keyless signing
- Workflow: `.github/workflows/mios-ci.yml`.
- Keyless OIDC: `cosign sign --yes <image-digest>`; trust roots in
  `etc/containers/policy.json` + `automation/42-cosign-policy.sh`.
- Cosign v2 binary downloaded by `automation/42-cosign-policy.sh`.

### LAW 3: BOUND-IMAGES
Every Quadlet `Image=` ref is symlinked into
`usr/lib/bootc/bound-images.d/<container-name>.container` so bootc fetches the
image alongside the host on every `bootc upgrade`. This is *why the AI
containers ship inside the image* -- the inference lanes and the agent
datastore are version-locked to the OS, not pip-installed daemons. Binder loop
in `automation/01-system-files-overlay.sh`.

---

## §3. Build pipeline

The build pipeline is the first half of the system lifecycle: it assembles the
single OCI image that the bootc lifecycle then carries forward. The scripts
that stand up the AI plane (inference lanes, agent units, the pgvector schema)
are just more numbered steps -- the same mechanism that installs packages also
stands up the brain.

### Containerfile shape (single-stage + ctx scratch)
```
FROM scratch AS ctx                 # build context staging
COPY automation/ usr/ etc/ ... → /ctx/
COPY VERSION                      → /ctx/VERSION
COPY config/artifacts/            → /ctx/bib-configs/
COPY tools/                       → /ctx/tools/

FROM ${BASE_IMAGE}                  # main build stage
LABEL ...
CMD ["/sbin/init"]
ARG MIOS_IDENTITY_USERNAME=mios
ARG MIOS_IDENTITY_HOSTNAME=mios
ARG MIOS_DESKTOP_FLATPAKS=
RUN --mount=type=bind,from=ctx,...
    --mount=type=cache,...
    set -ex;
    cp -a /ctx/* /tmp/build/;
    # CRLF -> LF normalization over all text files (Windows build hosts);
    install_packages_strict base;            # Containerfile pre-pipeline
    bash /tmp/build/automation/01-system-files-overlay.sh;  # overlay
    /tmp/build/automation/build.sh;          # phase-script orchestrator
    dnf clean all;
    rm -rf /tmp/build;
    find /var ! -name tmp ! -name cache -delete;
    find /run ! -name secrets -delete
RUN bootc completion bash > /etc/bash_completion.d/bootc
RUN bash /ctx/tools/mios-sysext-pack.sh /usr/lib/extensions/source || true
RUN ostree container commit
RUN bootc container lint            # LAW 4 (FINAL RUN)
```

### Phase script table (`automation/[NN]-*.sh`)

`automation/build.sh` iterates every numbered script in lex order. Per-script
`set +e`/`set -e` wrapping captures failures into `FAIL_LOG`/`WARN_LOG` instead
of aborting. Critical packages are post-validated via `rpm -q` against the
`critical` package set.

| # | Script | Purpose |
|---|---|---|
| 01 | 01-repos.sh | RPMFusion + Terra + dnf-plugins-core + dnf5-plugins repo setup |
| 02 | 02-kernel.sh | kernel-modules-extra/devel/headers/tools (no kernel upgrade) |
| 05 | 05-enable-external-repos.sh | Terra + nvidia-container-toolkit + Microsoft repos |
| 08 | 01-system-files-overlay.sh | usr/+etc/ overlay; bound-images binder loop |
| 10 | 10-gnome.sh | GNOME 50 + Bibata cursor + Phosh |
| 11 | 11-hardware.sh | mesa + AMD ROCm + Intel + NVIDIA akmod |
| 12 | 12-virt.sh | KVM/QEMU + libvirt + Looking Glass build deps |
| 13 | 36-ceph-k3s.sh | Ceph client + k3s binary download |
| 15 | 34-render-quadlets.sh | Render ${MIOS_*} placeholders in Quadlet units |
| 18 | 18-apply-boot-fixes.sh | USBGuard perms + 203/EXEC chmod fix + 217/USER systemd-resolved fix |
| 19 | 37-k3s-selinux.sh | k3s-selinux policy compile (shipped, not loaded) |
| 20 | 20-fapolicyd-trust.sh | fapolicyd trust DB (initial seed) |
| 20 | 20-services.sh | Service preset/drop-in cleanup |
| 21 | 21-moby-engine.sh | moby-engine + buildx parity |
| 22 | 22-freeipa-client.sh | sssd + ipa-client (Day-2 enrollment optional) |
| 23 | 23-uki-render.sh | UKI artifact render via systemd-ukify |
| 25 | 25-firewall-ports.sh | firewalld permanent port set |
| 26 | 26-gnome-remote-desktop.sh | GNOME-RD enable; xrdp masked |
| 30 | 30-locale-theme.sh | Locale + dark theme (skel-based) |
| 31 | 31-user.sh | PAM authselect + sysusers user create |
| 32 | 32-hostname.sh | Default hostname template (`mios-XXXXX` derivable) |
| 33 | 33-firewall.sh | mios-firewall-init libexec + zone defaults |
| 34 | 34-gpu-detect.sh | mios-gpu-detect service bridge |
| 35 | 35-gpu-passthrough.sh | VFIO PCI passthrough kargs + binder rules |
| 35 | 35-gpu-pv-shim.sh | GPU paravirt shim |
| 35 | 35-init-service.sh | mios-init.service formal target transitions |
| 36 | 36-akmod-guards.sh | akmods.service condition gating |
| 36 | 36-tools.sh | Operator utility install (htop, jq, etc.) |
| 37 | 37-aichat.sh | aichat / aichat-ng binary download |
| 37 | 37-flatpak-env.sh | /usr/lib/mios/env.d/flatpaks.env capture |
| 37 | 38-selinux.sh | semanage booleans + fcontext rules |
| 38 | 38-vm-gating.sh | Hyper-V vsock + GNOME-RD setup |
| 38 | 38-sglang-prep.sh | Opt-in offline bake of the SGLang heavy-lane weights (gated) |
| 38 | 38-vllm-prep.sh | Opt-in offline bake of the vLLM heavy-lane weights (gated) |
| 39 | 39-desktop-polish.sh | Desktop entries + MOTD + fastfetch |
| 40 | 40-composefs-verity.sh | composefs verity for /usr immutability |
| 42 | 42-cosign-policy.sh | cosign v2 download + policy bake |
| 43 | 43-uupd-installer.sh | uupd Day-2 update path |
| 44 | 44-podman-machine-compat.sh | Podman-machine compatibility shim |
| 45 | 45-nvidia-cdi-refresh.sh | NVIDIA CDI generation timing |
| 46 | 46-greenboot.sh | greenboot health checks |
| 47 | 47-hardening.sh | secureblue-derived sysctl + kernel hardening |
| 49 | 49-finalize.sh | systemd preset-all + image metadata + cred scrub |
| 50 | 50-enable-log-copy-service.sh | mios-copy-build-log.service enable |
| 52 | 52-bake-kvmfr.sh | KVMFR kernel module bake |
| 53 | 53-bake-lookingglass-client.sh | Looking Glass B7 client bake (cmake/make) |
| 90 | 90-generate-sbom.sh | syft CycloneDX SBOM emission |
| 98 | 98-boot-config.sh | Boot config finalization |
| 99 | 99-cleanup.sh | Cache + tmp cleanup |
| 99 | 99-postcheck.sh | Build-time invariant validation (see §15) |

Skipped under in-Containerfile build: `01-system-files-overlay.sh` runs
pre-pipeline directly from the Containerfile. The heavy-lane weight bakes
(`38-sglang-prep.sh`, `38-vllm-prep.sh`) are **opt-in and empty by default** --
they only fetch multi-GB weights when `[ai.sglang].bake_model` /
`[ai.vllm].bake_model` is set, so no model bloats a default image.

### Sub-phase numbering
The numeric prefix encodes execution order. Multiple scripts share a prefix
(20, 35, 36, 37, 38, 99) when they're peer concerns at the same stage.

---

## §4. Software Bill of Materials

Single source of truth: `usr/share/mios/mios.toml`. Every RPM lives under a
`[packages.<category>].pkgs` array parsed by `automation/lib/packages.sh`.

Helpers (provided by `automation/lib/packages.sh`):
- `install_packages "<category>"` -- best-effort, `--skip-unavailable`.
- `install_packages_strict "<category>"` -- fails the script on any miss.
- `install_packages_optional "<category>"` -- pure best-effort, never fails.

Representative categories and their purposes:

| Category | Purpose |
|---|---|
| repos | RPM repo enablement (no name installs) |
| base | Security stack, first-pass install (Containerfile pre-pipeline) |
| moby | moby-engine for Docker-API parity |
| uki | systemd-ukify for UKI builds |
| sbom-tools | syft |
| k3s-selinux-build | SELinux policy build chain |
| kernel | kernel-modules-extra/devel/headers/tools |
| gnome | GNOME 50 desktop |
| gnome-core-apps | GNOME core apps |
| gpu-mesa | Mesa userspace + Vulkan |
| gpu-amd-compute | AMD ROCm |
| gpu-intel-compute | Intel oneAPI / NEO |
| gpu-nvidia | NVIDIA proprietary stack |
| virt | KVM/QEMU + libvirt + Looking Glass build deps + KVMFR |
| containers | Podman, runc, conmon, netavark, slirp4netns, fuse-overlayfs |
| self-build | The image's own build toolchain |
| boot | Bootloader, plymouth, grubby, dracut |
| cockpit | Cockpit web management |
| wintools | Windows VM tooling |
| security | SELinux, fapolicyd, USBGuard, audit, openscap, AIDE |
| gaming | Steam runtime, Proton, Lutris |
| guests | Guest agents (virtio, spice) |
| storage | LVM, MD, multipath, ZFS, BTRFS, XFS |
| ceph | Ceph client/server |
| k3s | k3s prerequisites (binary downloaded separately) |
| ha | Pacemaker/Corosync |
| utils | Operator utilities |
| android | Waydroid + binder |
| looking-glass-build | Looking Glass build chain |
| cockpit-plugins-build | Cockpit plugin compilation |
| network-discovery | mDNS, Avahi, SSDP, llmnr |
| phosh | Phosh mobile session |
| updater | uupd, BIB, rpm-ostree |
| freeipa | FreeIPA / SSSD client |
| ai | Local AI runtime (inference lanes, agent stack deps) |
| critical | Post-install `rpm -q` validation list |
| bloat | Removed packages |
| nut | Network UPS Tools |

**Full enumeration: see [`MiOS-SBOM.csv`](MiOS-SBOM.csv) and
`usr/share/doc/mios/reference/PACKAGES.md`.**

### Kernel rule (LAW-adjacent)
ONLY add: `kernel-modules-extra`, `kernel-devel`, `kernel-headers`,
`kernel-tools`. NEVER upgrade `kernel`/`kernel-core` in-container --
`automation/01-repos.sh` excludes them explicitly. dnf option spelling is
`install_weak_deps=False` (underscore); `install_weakdeps` is silently ignored
by dnf5.

---

## §5. From-source components

| Component | Build script | Source |
|---|---|---|
| **Looking Glass B7 client** | `automation/53-bake-lookingglass-client.sh` | git clone + cmake/make/install |
| **KVMFR kernel module** | `automation/52-bake-kvmfr.sh` | upstream gnif/LookingGlass tree |
| **k3s binary** | `automation/36-ceph-k3s.sh` | github.com/k3s-io/k3s releases |
| **k3s-selinux policy** | `automation/37-k3s-selinux.sh` | k3s-io/k3s-selinux |
| **Custom SELinux modules** | `usr/share/selinux/packages/mios/*.te` | Compiled per-rule, shipped, NOT loaded at build (loaded post-build via systemd) |
| **cosign v2** | `automation/42-cosign-policy.sh` | github.com/sigstore/cosign releases |
| **aichat / aichat-ng** | `automation/37-aichat.sh` | github.com/sigoden/aichat + blob42/aichat-ng |
| **Bibata cursor theme** | `automation/10-gnome.sh` | tarball download |

### LAW-relevant from-source policy
- Looking Glass + KVMFR build during `12-virt.sh` toolchain install; cmake/
  gcc/*-devel removed before image commit (image stays slim) -- see
  `automation/53-bake-lookingglass-client.sh`.
- SELinux modules ship as `.te` source AND compiled `.pp`; load happens at boot
  via `mios-selinux-init.service`, NOT during build (avoids composefs-verity
  breakage).
- The AI heavy-lane weights (SGLang/vLLM) are an opt-in offline bake, not a
  from-source compile -- empty by default (§3).

---

## §6. System overlay

### kargs.d (`usr/lib/bootc/kargs.d/*.toml`)

Flat array form only -- bootc rejects `[kargs]` section headers and `delete`
sub-keys.

| File | Kargs | Purpose |
|---|---|---|
| 00-mios.toml | `init=/sbin/init`, `audit=1`, `lockdown=integrity`, `iommu=pt`, `intel_iommu=on`, `amd_iommu=on` | Core boot / IOMMU |
| 01-mios-hardening.toml | `slab_nomerge`, `randomize_kstack_offset=on`, `vsyscall=none`, `oops=panic`, `module.sig_enforce=1` | Kernel hardening (note: `init_on_alloc/free` and `page_alloc.shuffle` deliberately disabled -- NVIDIA/CUDA incompatibility) |
| 02-mios-gpu.toml | NVIDIA/AMD/Intel GPU silicon-specific flags | Per-vendor GPU workarounds |
| 10-mios-verbose.toml | (commented-out by default) | Verbose boot for debugging |
| 10-nvidia.toml | `nvidia.NVreg_PreserveVideoMemoryAllocations=1` etc. | NVIDIA module overrides |
| 13-rtx50-vfio-workaround.toml | `pcie_acs_override=...`, RTX 50 idle workarounds | Blackwell VFIO/idle fix |
| 15-rootflags.toml | `rootflags=...` | Root mount options |
| 20-vfio.toml | `vfio-pci.ids=...` (placeholder) | VFIO passthrough |
| 30-security.toml | secureblue-derived flags | Extended hardening |
| 31-secureblue-extended.toml | additional secureblue kargs | Extended secureblue |

### sysctl.d (`usr/lib/sysctl.d/*.conf`)
- `90-mios-le9uo.conf` -- BORE/le9uo scheduler tuning (keys prefixed `-` so
  missing-on-vanilla kernels is silent).
- `90-mios-overlayfs.conf` -- overlay/sysext tuning.
- `99-mios-hardening.conf` -- TCP/IP hardening + `unprivileged_userns_clone`
  (also `-`-prefixed for kernel portability).

### modprobe.d (`usr/lib/modprobe.d/*.conf`)
- `nvidia-open.conf` -- open kernel module flag (managed at /usr to prevent
  /etc state drift).
- `blacklist-vmw_vsock.conf` -- blacklists VMware vsock (conflicts with
  Hyper-V hv_sock).

### modules-load.d (`usr/lib/modules-load.d/mios.conf`)
```
ntsync       # Wine NT sync (kernel 6.10+; cosmetic warn on WSL2 6.6)
vfio-pci
hv_sock
ceph
rbd
```

### tmpfiles.d (`usr/lib/tmpfiles.d/*.conf`)
Every `/var/*` and `/run/*` directory used by 'MiOS' is declared here, including
the AI-plane data dirs (`mios-pgvector.conf` for the PostgreSQL PGDATA parent,
`mios-llamacpp.conf` for the KV slot-save dir). Files include: `mios.conf`,
`mios-backup.conf`, `mios-ceph.conf`, `mios-cpu.conf`, `mios-crowdsec.conf`,
`mios-freeipa.conf`, `mios-gpu.conf`, `mios-grd.conf`, `mios-infra.conf`,
`mios-iommu.conf`, `mios-ipa.conf`, `mios-k3s.conf`, `mios-llamacpp.conf`,
`mios-nfs.conf`, `mios-pgvector.conf`, `mios-pxe.conf`, `mios-virtio.conf`,
`mios-wsl2-hacks.conf`.

LAW 2 enforcement: build-time writes to `/var/` are forbidden. The overlay step
in `automation/01-system-files-overlay.sh` writes home dotfiles to `/etc/skel/`
and lets `systemd-sysusers` populate `/var/home/<user>/` at first boot.

### sysusers.d (`usr/lib/sysusers.d/*.conf`)
Canonical: `10-mios.conf` -- declares `g mios 1000` (numeric GID lookup required
by `u mios 1000:mios`). Critical: login users MUST have fixed UIDs >= UID_MIN
(1000). Auto-allocation (`-`) picks from the system range (< 1000) and breaks
logind/XDG_RUNTIME_DIR. Postcheck #8/#8b enforce.

Service users: `50-mios.conf` (mios-virt UID 800), `50-mios-services.conf`,
`50-mios-gpu.conf` (kvm/video/render GIDs pinned), `20-podman-machine.conf`
(`g core 1001` + `u core 1001:core`). The AI/SYSTEM tier identities are declared
in `50-mios-services.conf`:

- Bucket groups for shared-state RBAC: `mios-ai` (GID **850**), `mios-sys`
  (GID **860**). Cross-agent reads happen via `chgrp mios-ai` + `0640`, never
  sudo.
- `mios-ai` (UID **850**) -- the core AI-agent user (Hermes/agent-pipe/opencode
  run under it; HOME `/var/lib/mios/hermes`).
- `mios-pgvector` (UID **826**) -- owns `/var/lib/mios/pgvector` (the PostgreSQL
  PGDATA parent); member of `mios-ai` so agents can read.
- `mios-llamacpp` (UID **827**) -- owns `/var/lib/mios/llamacpp` (the
  mios-llm-light KV slot-save dir); member of `mios-ai`.
- Legacy `mios-hermes` (820) + `mios-agent-pipe` (822) accounts are RETAINED
  inert. `mios-ollama` (815) is retained inert as a historical GPU-inference
  sibling (Ollama itself is removed; see Appendix B).
- Service sidecars (guacamole/guacd/postgres/pxe-hub/crowdsec) occupy the
  810-819 range.

### dracut
- `usr/lib/dracut/dracut.conf.d/*-mios-*.conf` -- the only MiOS-authored dracut
  surface (5 drop-ins: 10-mios-generic, 50-mios-hyperv, 51-mios-virtio,
  52-mios-nvidia-exclude, 90-mios-verify). These layer over whatever the dracut
  RPM ships; we no longer carry verbatim copies of dracut binaries or
  `modules.d/` (dropped per audit finding F10 -- carrying upstream snapshots
  silently shadowed newer RPMs on update). The dracut RPM itself is pulled by
  the base image.

---

## §7. Quadlet sidecars

All MiOS-owned Quadlets follow LAW 6: declare `User=`, `Group=`, `Delegate=yes`.
Documented root exceptions are flagged per-unit (Ceph, k3s, the upstream
GPU-inference images that probe `nvidia-smi`). Sidecars live under
`usr/share/containers/systemd/`; ceph/k3s headers retain a comment pointing at
the historical `/etc/containers/systemd/` path.

### AI inference lanes (named by *function*, not by upstream tool)

| Unit | Container | Port key | Role |
|---|---|---|---|
| `mios-llm-light.container` | `mios-llm-light` | `llm_light` | **Primary** lane: `llama.cpp` multi-model server fronted by the `mios-llm-light` proxy image (`ghcr.io/mostlygeek/llama-swap:cuda`). Auto-swaps the everyday chat/reasoning models, KV-pages each conversation to disk, **and** serves embeddings (`nomic-embed-text`, OpenAI-compat `/v1/embeddings`) plus the `mios-opencode` coder model. User/Group `827`/`827`. Config: `usr/share/mios/llamacpp/mios-llm-light.yaml` |
| `mios-llm-heavy.container` | `mios-llm-heavy` | `vllm` | Heavy GPU reasoning lane (vLLM, served-name `mios-heavy`). VRAM-gated, off by default; User/Group `815`/`815` |
| `mios-llm-worker@.container` | `mios-llm-worker@` | -- | Single-model swarm workers (templated, for the dGPU swarm topology) |

> These lanes speak the OpenAI/Ollama-compatible API -- any OpenAI-API client
> talks to them unchanged -- but the inference *engine* is
> `llama.cpp`/SGLang/vLLM, not a hosted service. The heavy lanes stay inert
> until baked + enabled + reachable (health-gated in `mios.toml`).

### Agent-plane datastore

- `mios-pgvector.container` (`mios-pgvector`, `:5432`, User/Group `826`/`826`) --
  PostgreSQL + pgvector, the **unified agent datastore** (FOSS replacement for
  the removed legacy datastore/Qdrant). Schema in
  `usr/share/mios/postgres/schema-init.sql`. Accessed via `mios-pg-query`
  (pure-python loopback client) and `mios-db --pg`.

### Infrastructure sidecars

| Unit | Container | User/Group | Notes |
|---|---|---|---|
| `mios-ceph.container` | `mios-ceph` | `root`/`root` | Documented exception -- Ceph requires uid 0 |
| `mios-k3s.container` | `mios-k3s` | `root`/`root` | Documented exception -- k3s requires uid 0 |
| `mios-pxe-hub.container` | `mios-pxe-hub` | `mios-pxe-hub` | `!wsl, !container` |
| `mios-guacamole.container` | `mios-guacamole` | `mios-guacamole` | After `mios-guacamole-postgres.service` |
| `mios-guacd.container`, `mios-guacamole-postgres.container`, `mios-crowdsec-dashboard.container` | -- | per-service | Renamed from the legacy `guacd`/`guacamole-postgres`/`crowdsec-dashboard` |
| `mios-searxng.container` | `mios-searxng` | per-service | SearXNG metasearch (port key `searxng`), backs `web_search` |
| `mios-open-webui.container` | `mios-open-webui` | per-service | Open WebUI front-end (port key `open_webui`) |

---

## §8. Systemd services

70+ MiOS-owned units across `usr/lib/systemd/system/`. The AI plane is
host-native where it needs the host's GPU/PATH (the orchestrator + gateway),
and containerized where isolation is cheap (the lanes + datastore). Grouped:

### AI / agent plane (host-native units + the Quadlet lanes above)
- `mios-agent-pipe.service` (port key `agent_pipe`) -- the standalone orchestrator: router +
  refine + council/swarm fan-out + critic/polish. The front door every gateway
  (OWUI, Discord, the `mios` CLI) talks to; it fans out and dispatches tools,
  then fronts Hermes. Code at `usr/lib/mios/agent-pipe/server.py`.
- `mios-gateway-agent.service` (port key `hermes`) -- the OpenAI-compatible agent gateway:
  sessions, the tool-loop, skills, browser/CDP control. The default sub-agent.
- `mios-delegation-prefilter.service` (port key `prefilter`) -- injects
  `tool_choice=delegate_task` on fan-outable prompts and forwards to Hermes
  (currently disabled by default in `mios.toml`).
- `mios-opencode-gateway.service` (port key `opencode_gateway`) -- opencode -> OpenAI `/v1` gateway
  shim that makes opencode a real council peer (loopback only).
- All resolve their endpoint from `MIOS_AI_ENDPOINT` (LAW 5) -- never a
  hard-coded port or vendor URL.

### Targets (role hierarchy)
- `mios-firstboot.target` -- Wants= cdi-detect, libvirtd-setup, grd-setup
- `mios-desktop.target`, `mios-headless.target`, `mios-hybrid.target`
- `mios-k3s-master.target`, `mios-k3s-worker.target`, `mios-ha-node.target`

### Firstboot services
- `mios-wsl-firstboot.service` -- WSL2 user creation + hostname + passwd
- `mios-wsl-init.service` -- WSL2 boot init
- `mios-wsl-runtime-dir.service` -- `/run/user/<uid>/` fallback (LAW-style fallback for non-PAM session paths)
- `mios-grd-setup.service` -- GNOME Remote Desktop firstboot (TLS keygen)
- `mios-cdi-detect.service` -- CDI generation (also orders the GPU inference lanes)
- `mios-libvirtd-setup.service` -- libvirtd firstboot
- `mios-firstboot.target` -- pulls the above together

### GPU services
- `mios-gpu-amd.service` -- AMD ROCm/KFD plumbing
- `mios-gpu-intel.service` -- Intel iGPU/i915/xe plumbing
- `mios-gpu-nvidia.service` -- NVIDIA module load + CDI ordering
- `mios-gpu-status.service` -- GPU passthrough status writer

### Service drop-ins (`*.service.d/`)
- `10-bare-metal-only.conf` -- `ConditionVirtualization=no` (corosync,
  crowdsec, multipathd, nfs-server, nvidia-powerd, osbuild-*, pacemaker,
  pcsd, smb, nmb, mios-ha-bootstrap)
- `10-mios-wsl2.conf` -- `ConditionVirtualization=!wsl` (avahi, cloud-*,
  greenboot-healthcheck, qemu-guest-agent, rpm-ostree-fix-shadow-mode,
  stratisd, systemd-homed, systemd-logind, virtlxcd, zincati)
- `10-mios-virt-gate.conf` -- virtualization gating (audit*, bootloader-update,
  ceph-bootstrap, chronyd, fapolicyd, firewalld, gdm, nvidia-powerd, tuned,
  usbguard, waydroid-container)
- `10-virt-gate.conf` -- applies to 'MiOS' units skipping in containers/WSL
- `10-mios-container-gate.conf` -- NetworkManager + systemd-resolved gating

### Timers
- `uupd.timer` (Day-2 updates)
- `podman-auto-update.timer`
- `mios-firstboot.timer` (one-shot via target)

---

## §9. Greenboot health checks

### Required (boot fails on failure)
- `usr/lib/greenboot/check/required.d/10-mios-role.sh` -- verify role applied

### Wanted (warn, don't fail)
- (Standard upstream Fedora-bootc + greenboot defaults)

### Failure handling
- `usr/lib/greenboot/fail.d/00-log-fail.sh` -- captures journalctl --failed to
  `/var/log/greenboot.fail` before rollback.

---

## §10. Security stack -- 10 layers

Defense-in-depth is the third pillar of the whole-system posture: it is what
makes "the OS that runs its own agents" trustworthy rather than reckless. The AI
plane runs unprivileged (LAW 6) inside this stack.

1. **Kernel kargs** (`usr/lib/bootc/kargs.d/*.toml`) -- `lockdown=integrity`,
   `slab_nomerge`, `randomize_kstack_offset=on`, `vsyscall=none`, `oops=panic`,
   `module.sig_enforce=1`. NOT: `init_on_alloc/free`, `page_alloc.shuffle`
   (NVIDIA incompat -- see `usr/share/doc/mios/guides/security.md`).
2. **sysctl** (`usr/lib/sysctl.d/99-mios-hardening.conf`) -- TCP/IP hardening,
   ASLR, ptrace_scope, dmesg_restrict.
3. **SELinux modules** (`usr/share/selinux/packages/mios/*.te`) -- per-rule
   custom modules; booleans + fcontexts via semanage in `38-selinux.sh`.
4. **fapolicyd** (`etc/fapolicyd/fapolicyd.rules`, `usr/lib/fapolicyd/`) --
   zero-trust deny-by-default; trust DB seeded in `20-fapolicyd-trust.sh`.
5. **CrowdSec** (`mios-crowdsec-dashboard` Quadlet + host bouncer) -- sovereign
   IPS mode; firewall-bouncer wires to firewalld.
6. **USBGuard** -- deny-by-default device policy; permissions enforced via
   `automation/18-apply-boot-fixes.sh`.
7. **firewalld** -- default zone `drop`; service set in `33-firewall.sh`.
8. **Audit / AIDE / OpenSCAP** -- audit subsystem present; AIDE policy shipped;
   OpenSCAP profile bound to PCI-DSS / DISA-STIG.
9. **composefs verity** (`automation/40-composefs-verity.sh`) -- `/usr` is
   verity-sealed read-only; tampering detected at boot.
10. **TPM2 / Clevis + image signing** -- cosign keyless OIDC chain; MOK keys at
    `etc/pki/mios/mok.der` (public); private key encrypted in GitHub secret per
    `automation/generate-mok-key.sh`.

---

## §11. AI / Agent surface

This section is the engineering map of the "agentic AI OS" half. The full
request/response contract is in [`api.md`](api.md); the agent-facing contract is
under [`/usr/share/mios/ai/`](../../../mios/ai/). The end-to-end shape is:

> **front-end (OWUI / Discord / `mios` CLI) -> agent-pipe (router + fan-out) ->
> Hermes (tool-loop gateway) -> inference lanes (generation + embeddings) ->
> pgvector (memory) -> MCP (tools) / A2A (peer agents).**

- **Canonical front door:** `mios-agent-pipe.service` on the `agent_pipe` port `/v1`
  (OpenAI-compatible). It refines the prompt, decomposes/fans out across a
  council/swarm, dispatches tool/verb calls, and polishes the reply. Every
  agent and tool resolves the endpoint from `MIOS_AI_ENDPOINT` (LAW 5).
- **Agent gateway:** `mios-gateway-agent.service` at the `hermes` port `/v1` -- the default
  sub-agent; owns sessions, the tool-loop, skills, and browser/CDP control.
- **Sub-agent registry:** `[agents.*]` in `/usr/share/mios/mios.toml` (SSOT);
  manifest mirror at `/usr/share/mios/ai/v1/agents.json`.
- **Inference backends (named by function):**
  - `mios-llm-light.service` -- **primary**, port key `llm_light` (llama.cpp via the
    `mios-llm-light` proxy image). Serves everyday chat/reasoning models, KV-pages
    per conversation, **and** embeddings (`nomic-embed-text`,
    `/v1/embeddings`) + the `mios-opencode` coder model. Model map:
    `usr/share/mios/llamacpp/mios-llm-light.yaml`.
  - `mios-llm-heavy.service` -- vLLM heavy lane, port key `vllm` (served-name
    `mios-heavy`), VRAM-gated/off by default.
  - `mios-llm-worker@.service` -- single-model swarm workers.
- **Memory:** PostgreSQL + pgvector (`mios-pgvector`, `:5432`) -- the unified
  agent datastore. Tables (`usr/share/mios/postgres/schema-init.sql`):
  `agent_memory`, `event`, `tool_call`, `session`, `skill`, `scratch`,
  `knowledge`, `sys_env`, `kanban`, `directory_entry`, `person`,
  `agent_keypair`, ... `nomic-embed-text` (served by `mios-llm-light`) provides
  the embeddings for `knowledge`/RAG vector recall. Accessed via `mios-pg-query`
  / `mios-db --pg`.
- **Tools & federation:** agents call tools over **MCP** and reach peer agents
  over **A2A**; `web_search` is backed by local **SearXNG** (port key `searxng`); the
  coder peer is served through the **opencode-gateway** (port key `opencode_gateway`).
- **Vendor system prompt:** `/usr/share/mios/ai/system.md`.
- **Hermes seed persona:** `/usr/share/mios/ai/hermes-soul.md` (slim, per-turn)
  + `/usr/share/mios/ai/hermes-soul-full.md` (on-demand examples + recipes).
- **Host override:** `/etc/mios/ai/system-prompt.md`.
- **Per-user override:** `~/.config/mios/system-prompt.md`.
- **MCP discovery:** `/usr/share/mios/ai/v1/mcp.json` (empty by default; opt-in
  via `/etc/mios/ai/v1/mcp.json` overlay).
- **Model metadata:** `/usr/share/mios/ai/v1/models.json`.
- **CLI:** `/usr/bin/mios` (reads `MIOS_AI_ENDPOINT`, falls back to the
  agent-pipe front door on the `agent_pipe` port).
- **KB delivery:** `/usr/share/mios/kb/manifest.json` (FHS-compliant location
  after the `proc/mios/` migration).
- **OpenAI tool schemas:** `/usr/lib/mios/tools/responses-api/*.json` +
  `/usr/lib/mios/tools/chat-completions-api/*.json`.
- **Structured output schemas:** `/usr/lib/mios/schemas/*.json`.
- **Sample API payloads:** `/usr/share/mios/api/{chat,responses,embeddings,
  batch.requests,mcp.tool}.{json,jsonl}`.
- **Sanitization tooling:** `tools/ascii-sweep.py` (typography + emoji scrub
  across `git ls-files`), `automation/99-postcheck.sh` (vendor-URL / Quadlet
  `User=` / bound-images-coverage lints).

> The early Ollama/the legacy datastore/Qdrant stack is fully removed. Ollama survives only
> as an *upstream API-compat reference* (the lanes speak the OpenAI/Ollama-
> compatible API) and in historical migration notes; it is not a live MiOS
> backend.

---

## §12. Build modes and output targets

### 5 build modes
1. **CI (`.github/workflows/mios-ci.yml`)** -- build -> rechunk on tag -> cosign
   keyless sign -> push to GHCR.
2. **Linux local (`Justfile`)** -- `just build` -> `localhost/mios:latest`.
3. **Windows local (`mios-build-local.ps1`)** -- same, via rootful Podman
   machine on WSL2.
4. **Self-build** -- a running 'MiOS' host runs `just build` against the repo
   it shipped with. The image contains its own toolchain
   (`[packages.self-build]`). This is the literal "self-replicating" property:
   the OS can rebuild its own image.
5. **Bootstrap (mios-bootstrap repo)** -- Total Root Merge of `mios.git` +
   `mios-bootstrap.git` onto a bare Fedora host, then `just build` from there.

### Output targets (Justfile)

| Target | Output |
|---|---|
| `just build` | `localhost/mios:latest` (OCI image) |
| `just rechunk` | `${IMAGE_NAME}:${VERSION}` + `:latest` (5-10x smaller deltas) |
| `just raw` | `output/mios.raw` (RAW disk image, 80 GiB ext4 root) |
| `just iso` | `output/mios-installer.iso` (Anaconda installer) |
| `just qcow2` | `output/mios.qcow2` (QEMU; needs `MIOS_USER_PASSWORD_HASH`, `MIOS_SSH_PUBKEY`) |
| `just vhdx` | `output/mios.vhdx` (Hyper-V; needs `MIOS_USER_PASSWORD_HASH`, `MIOS_SSH_PUBKEY`) |
| `just wsl2` | `output/mios.wsl2.tar` (WSL2 import tarball) |
| `just sbom` | `artifacts/sbom/mios-sbom.json` (CycloneDX) |
| `just artifact` | Refresh AI manifests + KB + Wiki docs |
| `just all` | Every artifact in one shot |
| `just all-bootstrap` | build + rechunk + log to bootstrap repo |

---

## §13. CI/CD

`.github/workflows/mios-ci.yml`:

| Step | Action |
|---|---|
| 1 | Checkout `mios-dev/MiOS` |
| 2 | Lint: shellcheck (`SC2038` fatal), hadolint, TOML validate |
| 3 | `bootc container lint` (LAW 4) |
| 4 | `podman build` -> ghcr.io/mios-dev/mios:`<sha>` |
| 5 | On tag: `rechunk` -> `:${VERSION}` + `:latest` |
| 6 | cosign keyless OIDC sign (image-digest) |
| 7 | Push to GHCR (requires `packages: write` permission) |

---

## §14. Architectural Laws

These six laws are the contract that lets MiOS be both immutable and agentic at
once. Laws 1-4 keep the image deterministic, atomic, and self-contained so bootc
can upgrade/roll it back; Laws 5-6 keep the AI plane unified and least-privileged
so the agent stack stays portable and sandboxed. Enforced by build-time lint and
`automation/99-postcheck.sh`.

1. **USR-OVER-ETC** -- static config in `/usr/lib/<component>.d/`; `/etc/` is
   admin-override only. Documented exceptions are upstream-contract surfaces
   (`/etc/yum.repos.d/`, `/etc/nvidia-container-toolkit/`).
2. **NO-MKDIR-IN-VAR** -- every `/var/` path declared via
   `usr/lib/tmpfiles.d/*.conf`. Never write to `/var/` at build time. bootc
   forbids it; lint will fail.
3. **BOUND-IMAGES** -- every Quadlet image symlinked into
   `/usr/lib/bootc/bound-images.d/` (this is why the AI containers ship inside
   the image).
4. **BOOTC-CONTAINER-LINT** -- must be the final `RUN` of `Containerfile`. No
   `--squash-all` (strips OCI metadata bootc needs).
5. **UNIFIED-AI-REDIRECTS** -- every agent and tool targets `MIOS_AI_ENDPOINT`.
   No vendor-hardcoded URLs. The endpoint resolves to the local agent front door
   (the `mios-agent-pipe` orchestrator; Hermes at the `hermes` port `/v1` is the default
   sub-agent behind it).
6. **UNPRIVILEGED-QUADLETS** -- every Quadlet declares `User=`, `Group=`,
   `Delegate=yes`. Documented root exceptions: `mios-ceph`, `mios-k3s`, and the
   upstream GPU-inference images that must probe `nvidia-smi` as root
   (rationale in each unit header).

---

## §15. Known issues and footguns (hard-won lessons)

1. **WSL2 wsl.conf is byte-naive** -- em-dashes (any multibyte char) shift its
   line counter and surface as bogus `Expected ' ' or '\n' in /etc/wsl.conf:N`
   errors. Postcheck #7 enforces strict ASCII.
2. **systemd-sysusers `u name -` allocates from system range** (< UID_MIN).
   logind then refuses to create `/run/user/<uid>/`, breaking dbus user
   session, dconf, Wayland session services. Pin login UIDs to 1000+.
   Postcheck #8 enforces.
3. **`u name UID:NUM` requires `g name NUM` first** -- sysusers won't
   auto-create the group. Without the `g` line, sysusers fails with
   "please create GID NUM" and the user is never created. Postcheck #8b.
4. **`/var/run` is a symlink to `/run`** -- systemd-tmpfiles rejects entries
   whose path component is `/var/run/...` ("Line references path below
   /var/run"). Use `/run/...` directly. Postcheck #9 enforces.
5. **`/proc/mios/` is non-FHS** -- original KB delivery shipped to
   `proc/mios/manifest.json` as a "synthetic /proc surface", but FHS 3.0
   defines /proc as the kernel virtual filesystem. Moved to
   `/usr/share/mios/kb/manifest.json` for compliance.
6. **`((VAR++))` is forbidden under `set -e`** -- bash exits 1 when the
   pre-increment value is 0. Use `VAR=$((VAR + 1))`.
7. **`--squash-all` strips bootc OCI metadata** -- never use it. bootc relies on
   layer metadata for upgrade deltas (and BIB on `ostree.final-diffid`).
8. **`install_weakdeps` is silently ignored by dnf5** -- correct spelling is
   `install_weak_deps=False` (underscore, capital F).
9. **`init_on_alloc=1`, `init_on_free=1`, `page_alloc.shuffle=1` are
   incompatible with NVIDIA/CUDA** -- disable in 'MiOS' despite secureblue
   recommending them. See `usr/share/doc/mios/guides/security.md`.
10. **`lockdown=integrity` not `confidentiality`** -- confidentiality breaks too
    many 'MiOS' workloads (kexec, /dev/mem, suspend-to-disk).
11. **Never upgrade `kernel`/`kernel-core` in-container** -- bootc's
    composefs/UKI flow assumes the base-image kernel. Only add
    `kernel-modules-extra/devel/headers/tools`. `automation/01-repos.sh`
    excludes the upgrade.
12. **systemd-udev-settle is deprecated upstream** -- emits warnings forever.
    Replace with `systemd-udev-trigger.service` ordering.
13. **WSL2 kernel 6.6 lacks `ntsync`** -- modules-load.d entry generates a
    cosmetic "Failed to find module" warning. Bare-metal Fedora 6.10+ has it.
    Acceptable.
14. **PAM session not opened under `wsl -u root` + `su - mios`** -- logind skips
    creating `/run/user/<uid>/`, so dbus/dconf/Wayland session services break.
    `mios-wsl-runtime-dir.service` is the belt-and-suspenders fallback (creates
    the dir unconditionally on WSL2 boot).
15. **systemd-tmpfiles 'D' type with no argument is interpreted as "purge"** --
    always specify the args field (`-` for default age) to avoid wiping
    pre-existing data.
16. **BIB requires `/tmp/mios-bib-output` (or whatever Linux path) to exist
    BEFORE `podman run -v`** -- crun returns ENOENT otherwise. The
    `mios-build-local.ps1` pre-creates it via `podman machine ssh`.
17. **Sysusers files run lexicographically; `10-` runs before unprefixed
    base-distro files** -- duplicate `g <name> <gid>` lines are tolerated if the
    GID matches; mismatch fails the user creation.
18. **systemd Description= field is UTF-8-aware but most other fields aren't** --
    keep all unit file content ASCII-only outside Description= to avoid surprise.
    Postcheck enforces via `systemd-analyze verify`.
19. **`'MiOS'` (capital) in JSON keys breaks single-quote-wrapping policy** --
    quote-mios.py's regex skips bare-string-literal `"MiOS"` so identifier values
    are preserved. Without that exclusion the PowerShell `$WslName = "MiOS"`
    becomes `"'MiOS'"` and WSL imports a distro literally named `'MiOS'`.
20. **`init.mount: target is busy` on WSL2 shutdown** -- WSL2-specific quirk
    where /init can't be unmounted because the WSL relay process holds it.
    Cosmetic; not a 'MiOS' bug.
21. **Quadlet does NOT expand `${VAR:-default}`** -- the heavy-lane units carry
    `${MIOS_*}` placeholders that `automation/34-render-quadlets.sh` must render
    before the unit starts; an unrendered placeholder in `PublishPort`/`Exec`
    yields "invalid port format" / a literal-arg crash.
22. **CDI mount injects the WSL CUDA driver but not on the linker path** -- on
    WSL2, `nvidia.com/gpu=all` mounts `/usr/lib/wsl/lib/libcuda.so` but doesn't
    put it on `LD_LIBRARY_PATH`, so llama-server logged "no usable GPU found"
    and ran entirely on CPU. The `mios-llm-light` unit prepends `/usr/lib/wsl/lib`
    so llama.cpp detects CUDA0.

---

## §16. Component licenses

| Component | License | Notes |
|---|---|---|
| 'MiOS' proper (this repo) | Apache-2.0 | `LICENSE` |
| uCore-HCI base | Apache-2.0 | upstream |
| Fedora CoreOS base | various (mostly GPL/MIT) | upstream Fedora |
| Looking Glass B7 | GPL-2.0 | from-source build |
| KVMFR module | GPL-2.0 | from-source build |
| k3s | Apache-2.0 | binary download |
| k3s-selinux | Apache-2.0 | from-source build |
| cosign v2 | Apache-2.0 | binary download |
| llama.cpp + mios-llm-light (light lane) | MIT | OCI image (`ghcr.io/mostlygeek/llama-swap`) |
| SGLang (heavy lane) | Apache-2.0 | OCI image (gated) |
| vLLM (alt heavy lane) | Apache-2.0 | OCI image (gated) |
| PostgreSQL + pgvector (agent DB) | PostgreSQL License + pgvector | OCI image |
| Ceph | LGPL-2.1 | OCI image |
| Bibata cursor theme | GPL-3.0 | tarball download |
| dracut-logger.sh | GPL-2.0 | vendored upstream (Amadeusz Żołnowski) |
| systemd | LGPL-2.1+ | upstream Fedora |
| All RPMs | per individual SPEC | see `usr/share/doc/mios/reference/licenses.md` |
| NVIDIA proprietary driver | NVIDIA Software License Agreement | redistributable per RPMFusion |
| Microsoft Mono / .NET firmware | various Microsoft licenses | optional install |

Full audit in `usr/share/doc/mios/reference/licenses.md` and
`usr/share/doc/mios/reference/sources.md`.

---

## §17. SBOM generation paths

- **CycloneDX**: `automation/90-generate-sbom.sh` runs `syft` against the
  in-build image. Output: `/usr/lib/mios/logs/mios-sbom.cyclonedx.json`.
- **SPDX**: same script with `-o spdx-json`. Output beside CycloneDX.
- **Justfile target**: `just sbom` runs `syft` against `localhost/mios:latest`
  on a deployed host or in CI.
- **Manual**: `MiOS-SBOM.csv` (this delivery) -- generated by
  `tools/lib/generate-sbom.py` from `mios.toml [packages.*]` + Quadlet refs +
  from-source list + Flatpak defaults.

---

## §18. Variable conventions

All MiOS-owned env vars start with `MIOS_*`. Everything operator-tunable flows
from one SSOT (`mios.toml`) with a three-layer override (highest wins):

1. `~/.config/mios/mios.toml` (per-user, highest priority)
2. `/etc/mios/mios.toml` (host/admin, written by bootstrap)
3. `/usr/share/mios/mios.toml` (vendor defaults, immutable, shipped in image)

Shell/systemd consumers read the derived bridge `/etc/mios/install.env`; run
`mios-sync-env` after editing `mios.toml` to refresh it. Admin drop-ins under
`/etc/mios/env.d/*.env` and the vendor fallback `/usr/share/mios/env.defaults`
fill in any remaining gaps.

Canonical vars (see `usr/share/mios/env.defaults`):

| Variable | Default | Purpose |
|---|---|---|
| `MIOS_VERSION` | `0.2.x` | Image version |
| `MIOS_IDENTITY_USERNAME` | `mios` | Login user name |
| `MIOS_IDENTITY_HOSTNAME` | `mios` | Hostname |
| `MIOS_REPO_URL` | https://github.com/mios-dev/mios | System repo URL |
| `MIOS_BOOTSTRAP_REPO_URL` | https://github.com/mios-dev/mios-bootstrap | Bootstrap repo URL |
| `MIOS_IMAGE_NAME` | `ghcr.io/mios-dev/mios` | OCI image base name |
| `MIOS_IMAGE_TAG` | `latest` | OCI image tag |
| `MIOS_BASE_IMAGE` | `ghcr.io/ublue-os/ucore-hci:stable-nvidia` | Containerfile base |
| `MIOS_LOCAL_TAG` | `localhost/mios:latest` | Local build tag |
| `MIOS_BIB_IMAGE` | `quay.io/centos-bootc/bootc-image-builder:latest` | BIB |
| `MIOS_AI_ENDPOINT` | local OpenAI-compatible front door | Single endpoint every agent/tool targets (LAW 5; resolves to `mios-agent-pipe`) |
| `MIOS_AI_MODEL` | per `[ai].model` | Default chat model |
| `MIOS_AI_KEY` | `""` | API key (empty for local) |
| `MIOS_PORTS_LLM_LIGHT` | per `[ports].llm_light` | `mios-llm-light` primary inference lane |
| `MIOS_PORTS_PGVECTOR` | `5432` | PostgreSQL + pgvector agent datastore |
| `MIOS_INSTALL_ENV` | `/etc/mios/install.env` | Host install env file |
| `MIOS_WSLBOOT_DONE` | `/var/lib/mios/.wsl-firstboot-done` | Sentinel |

Build-time path constants (`automation/lib/paths.sh` + runtime
`/usr/lib/mios/paths.sh`):

| Variable | Value |
|---|---|
| `MIOS_USR_DIR` | `/usr/lib/mios` |
| `MIOS_LOG_DIR` | `/usr/lib/mios/logs` |
| `MIOS_LIBEXEC_DIR` | `/usr/libexec/mios` |
| `MIOS_SHARE_DIR` | `/usr/share/mios` |
| `MIOS_ETC_DIR` | `/etc/mios` |
| `MIOS_VAR_DIR` | `/var/lib/mios` |
| `MIOS_MEMORY_DIR` | `/var/lib/mios/memory` |
| `MIOS_SCRATCH_DIR` | `/var/lib/mios/scratch` |
| `MIOS_BUILD_LOG` | `/usr/lib/mios/logs/mios-build.log` |
| `MIOS_BUILD_CHAIN_LOG` | `/usr/lib/mios/logs/mios-build-chain.log` |

---

## §19. Hardware targeting

The hardware story ties the whole system together: the same GPU wiring (CDI)
lets the inference lanes claim a GPU *and* lets a VFIO passthrough VM claim
another, on one box.

### Supported topologies
- **AI workstation**: AMD/Intel CPU + NVIDIA dGPU (Blackwell RTX 50, Ada
  RTX 40, Ampere RTX 30) -- full CUDA; the `mios-llm-light` lane offloads to the
  dGPU and the gated heavy lanes (SGLang/vLLM) serve when VRAM allows.
- **Hyperconverged**: Single-node Ceph + k3s + KVM + Looking Glass --
  passthrough one GPU to a Windows VM, retain another for the host + inference.
- **Headless server**: AMD EPYC / Intel Xeon, no display, k3s-master role.
- **WSL2**: Windows host; the `mios-llm-light` lane reaches the dGPU through the
  WSL CDI mapping (`/run/cdi/wsl2-nvidia.yaml`).

### Specific silicon workarounds
- **RTX 50 Blackwell**: GB20*/GB10* detected at runtime by
  `usr/libexec/mios/role-apply` -- defaults to headless role to avoid VFIO reset
  bug; `13-rtx50-vfio-workaround.toml` adds idle-flush kargs.
- **NVIDIA**: open kernel module via `usr/lib/modprobe.d/nvidia-open.conf`.
- **AMD ROCm**: `/dev/kfd` + `/dev/dri/renderD*` permissions hardened in
  `mios-gpu-amd.service` (chgrp render, chmod 0660).
- **Intel iGPU/dGPU**: `i915` and `xe` drivers loaded; renderD128 access
  hardened in `mios-gpu-intel.service`.
- **Hyper-V Enhanced Session**: `mios-hyperv-enhanced.service` wires GNOME
  Remote Desktop over hv_sock vsock (replacing the deprecated xrdp path).

---

## §20. Quick-reference cheatsheet

```bash
# Build (Linux)
just preflight            # System prereq check
just build                # OCI -> localhost/mios:latest
just rechunk              # Day-2-friendly delta optimization
just iso / raw / qcow2 / vhdx / wsl2   # Disk images
just sbom                 # CycloneDX SBOM

# Build (Windows)
.\preflight.ps1
.\mios-build-local.ps1    # rootful podman + WSL2 + podman build

# User-space config (single SSOT: mios.toml)
just init-user-space      # seed ~/.config/mios/mios.toml
just edit                 # $EDITOR ~/.config/mios/mios.toml
just show-env             # resolved MIOS_* vars

# Day-2
bootc upgrade             # pull latest, stage
bootc rollback            # revert to previous
sudo systemctl reboot

# Diagnostics
journalctl -u mios-firstboot.target
journalctl -u mios-agent-pipe.service
journalctl -u mios-llm-light.service
cat /var/lib/mios/role.active
mios "ask the local AI a question"

# AI surface (front door is the agent-pipe on the agent_pipe port; lanes below it)
curl -s http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1/models | jq
curl -s http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1/models | jq   # mios-llm-light lane
curl -s http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1/embeddings \
  -d '{"model":"nomic-embed-text","input":"hello"}' -H 'Content-Type: application/json' | jq

# Agent datastore (PostgreSQL + pgvector)
mios-db --pg -c '\dt'

# Repo overlay (sanity)
ls /usr/lib/mios/             # paths.sh, logs/, agent-pipe/, agents/
ls /usr/share/mios/           # mios.toml, env.defaults, llamacpp/, postgres/, ai/, kb/
ls /etc/mios/                 # install.env, mios.toml, ai/, kb.conf.toml
ls /var/lib/mios/             # memory, scratch, embeddings/, llamacpp/, pgvector/, evals/
```

---

## Appendix A: FHS layout table

| Path | FHS character | bootc disposition | Source-of-truth in repo |
|---|---|---|---|
| `/usr` | Read-only, shareable | Immutable composefs mount; change = new OCI image | `usr/` overlaid by `automation/01-system-files-overlay.sh` |
| `/etc` | Host-specific config | 3-way merge overlay; admin edits survive upgrades | `etc/` |
| `/var` | Mutable, persistent | Fully writable; never replaced on upgrade | `usr/lib/tmpfiles.d/mios*.conf` (LAW 2) |
| `/srv` | Data served by the system | Persistent | `usr/lib/tmpfiles.d/mios.conf` |
| `/run` | Ephemeral runtime (FHS 3.0) | tmpfs; cleared at boot; never in image layers | -- |
| `/home` | User home directories | Persistent via `/var/home/<user>` + symlink | `usr/lib/sysusers.d/` |
| `/opt` | Add-on software packages | Used for `usr/share/mios/prompts/` | direct overlay |
| `/usr/local` | Local additions | `/usr/share/mios/cookbooks/` | direct overlay |

---

## Appendix B: Reconciliation against live repo state

### Migration: the AI plane moved off the early stack
The early Ollama / the legacy datastore / Qdrant stack is **fully removed**. Current state:

| Was | Now |
|---|---|
| the retired Ollama lanes (`ollama.service` / `mios-ollama-cpu.service`) | `mios-llm-light.service` (port key `llm_light`) -- llama.cpp via `mios-llm-light`; also serves embeddings + the coder model |
| `mios-sglang` / `mios-vllm` Quadlets | `mios-llm-heavy` (vLLM, port key `vllm`, gated) |
| `mios-llama-worker@` | `mios-llm-worker@` |
| the legacy datastore agent store (BSL 1.1) | PostgreSQL + pgvector (`mios-pgvector`, `:5432`; `mios-pg-query` / `mios-db --pg`) |
| Qdrant vector store | pgvector (the same Postgres engine) |
| `37-ollama-prep.sh` model bake | removed; `38-vllm-prep.sh` (opt-in, gated) |

Ollama survives only as an *upstream API-compat reference* (the lanes speak the
OpenAI/Ollama-compatible API, and `mios-llm-light`'s model map uses Ollama-style
tags) and in historical migration notes -- not as a live MiOS backend. The
`mios-ollama` (815) sysusers account is retained inert.

### Stale references collapsed/removed
- `~/.config/mios/env.toml`, `images.toml`, `build.toml`, `flatpaks.list`,
  `profile.toml`, bare `env` -- all collapsed into a single `mios.toml` (§18).
  Legacy fallback in `tools/lib/userenv.sh` if `mios.toml` is absent.
- `proc/mios/manifest.json` -- moved to `usr/share/mios/kb/manifest.json` for
  FHS compliance.
- `automation/install-fhs.sh` -- byte-identical to `automation/install.sh`,
  deleted.
- `system.md` (root) -- byte-identical to `system-prompt.md`, deleted.
- `build-mios.sh` (root) -- near-duplicate of `automation/build-mios.sh`,
  deleted.

### Canonical naming map
| Old | New |
|---|---|
| CloudWS-bootc | 'MiOS' / mios-dev |
| CloudWS-OS | 'MiOS' / mios-dev |
| `cloudws-*.container` | `mios-*.container` |
| `guacd` / `guacamole-postgres` / `crowdsec-dashboard` | `mios-guacd` / `mios-guacamole-postgres` / `mios-crowdsec-dashboard` |
| `cloudws-pxe-hub.container` / `cloudws-guacamole.container` | `mios-pxe-hub.container` / `mios-guacamole.container` |

The proper-noun spelling **`'MiOS'`** (single-quoted) is the legal-mark form for
display strings. Lowercase `mios` is the technical identifier used in paths, env
vars, package names, and code.
