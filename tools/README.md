<!-- AI-hint: Index of the standalone out-of-image toolkit scripts that prepare, verify, and maintain a host for MiOS — VFIO GPU/USB passthrough, CPU isolation/pinning, hardware profiling, Windows-VM Secure Boot/OVMF enrollment, the FHS overlay/sysext packer, and repo build/maintenance helpers. Use to understand what each tools/ script does and how it serves the MiOS build->image->bootc->agentic-AI lifecycle.
     AI-related: rtx4090-vfio-configurator, vfio-verify, vm-cpu-pin-manager, configure-xbox-cpu, system-profiler, profile-compare, run-all-profilers, mios-overlay, mios-sysext-pack, mios-upstream-monitor, preflight, log-to-bootstrap, generate-ai-manifest, generate-unified-knowledge -->
# 'MiOS' Toolkit Scripts

## Purpose

The shared native projection command is `mios-gen sync --root <checkout>`.
`--plan` validates every declared prerequisite and prints the SSOT stage plan
without writing files or changing the Git index. Execution follows
`[generation.sync]`, fails on child errors, and reports explicit intent-to-add
registration for new source files. It preserves all 23 existing projection
stages; `sync-generated.sh` is a compatibility dispatcher. The names-registry
implementation is also folded into `mios-gen names-registry`; its old binary
delegates to the same implementation.

`mios-gen bootstrap-sync --root <checkout> --bootstrap <bootstrap-checkout> --check`
checks the vendor `[bootstrap.sync]` manifest without writing either repository.
`--apply` preflights every authority file, TOML scope and tracked-file declaration,
then stages replacements and restoration copies before publication. A publication
failure rolls back earlier files and reports any restoration failures. Paths that
escape either root or traverse symlinks are rejected. Whole-table mirrors own
scalar keys; selected-key mirrors preserve bootstrap-owned neighbours and comments.
The old `sync-bootstrap.py` CLI delegates to this native command and fails when
the native generator is unavailable. Review local bootstrap contributions before
applying the declared authority changes.

`mios-gen names --root <checkout>` audits canonical names and compatibility
metadata without exposing configuration values. Runtime and globals consumers
share the resolver's canonical `section.key -> MIOS_SECTION_KEY` convention.

MiOS is one system built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is a single container image — boot it,
`bootc upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is
*also* a **local, self-replicating, agentic AI operating system** (local
inference lanes → agent orchestration → PostgreSQL+pgvector memory, all behind
one OpenAI-compatible endpoint).

This directory is **out-of-image tooling that surrounds that system rather than
shipping inside it.** The image itself is produced by the build pipeline
([`../Containerfile`](../Containerfile) + the numbered scripts in
[`../automation/`](../automation/)) and the system FHS overlay lives at
[`../`](../). The scripts *here* run **on a booted host** — either a 'MiOS' host,
or any Fedora/RHEL-family host being prepared to become one — to do the things
the image cannot do for itself from the outside:

- **Prepare host hardware** so MiOS's two GPU consumers can coexist: VFIO
  passthrough hands a discrete GPU to a KVM/QEMU VM (the gaming/Windows-VM
  path), while CPU isolation/pinning carves out cores for those VMs so they
  don't starve the host desktop or the local AI inference lanes.
- **Assess readiness** of a candidate host (virtualization, IOMMU, GPU,
  storage) *before* you commit it to `bootc switch`.
- **Fix Windows-VM Secure Boot / OVMF enrollment**, which is the fiddly part of
  the Looking-Glass passthrough story.
- **Maintain the repo and image** — overlay the FHS onto a dev host, pack
  sysexts, refresh AI manifests/knowledge, track upstream versions.

In short: the build pipeline makes the image and bootc carries it forward;
**these tools get the metal ready for that image and keep the source tree
healthy.**

> **All shell-convention rules from
> [`../usr/share/doc/mios/guides/engineering.md`](../usr/share/doc/mios/guides/engineering.md)
> ("Shell conventions") apply here too.** `set -euo pipefail` at the top;
> `VAR=$((VAR + 1))` not `((VAR++))` (the latter returns 1 under `set -e` when
> the result is 0); quote every expansion; prefer `compgen -G` / `find -exec` /
> `read -ra`; shellcheck-clean (SC2038 is fatal in CI).

---

## VFIO toolkit

For passing GPUs and USB controllers into KVM/QEMU VMs — the mechanism behind
MiOS's "hand a discrete GPU to a Windows VM and game on it" Looking-Glass path.
This works *because* MiOS stages `vfio-pci` kargs and ships KVM/libvirt in the
image; these scripts do the per-host binding and verification.

| Script | Purpose |
|--------|---------|
| `rtx4090-vfio-configurator.sh` | Opinionated RTX 4090 setup — finds the GPU + its audio function PCI IDs and writes `/etc/modprobe.d/vfio.conf` for passthrough |
| `vfio-verify.sh` | Verify VFIO binding — IOMMU kernel args, module load status, GPU host-lockout |

---

## CPU isolation & pinning

For pinning VM vCPUs to host physical cores and isolating cores from the Linux
scheduler. Cleanly partitioned cores are what let a passthrough VM run at native
speed without contending with the host GNOME session or the agent stack's
inference work.

| Script | Purpose |
|--------|---------|
| `vm-cpu-pin-manager.sh` | Manage libvirt hook scripts to pin VM CPU threads to specific physical cores (AMD Ryzen / Intel hybrid / NUMA-aware) |
| `configure-xbox-cpu.sh` | Xbox-style Windows-VM CPU pinning + host-passthrough libvirt XML configuration |

---

## Host profiling & assessment

Run these to inventory a host's hardware and virtualization capabilities —
ideally **before** deploying 'MiOS' to a new box, or afterward to diff a
configuration change.

| Script | Purpose |
|--------|---------|
| `system-profiler.sh` | Aggregate CPU / memory / GPU / storage / PCI / USB / IOMMU into text + JSON hardware-profile reports |
| `run-all-profilers.sh` | Chain the profilers (quick summary → IOMMU → full profiler) into one consolidated report |
| `profile-compare.sh` | Diff two profiler outputs (CPU / GPU / memory / kernel) — e.g. before/after a change or across two machines |

---

## Windows VM / Secure Boot helpers

For Looking-Glass-style Windows VMs that require Secure Boot + TPM 2.0. The
`.xml` file is a libvirt domain template; the shell scripts locate, patch, and
recover OVMF firmware and NVRAM enrollment.

| File | Purpose |
|------|---------|
| `check-ovmf-enrollment.sh` | Check whether the host has pre-enrolled Secure Boot `OVMF_VARS` (vs blank vars) |
| `get-secureboot-ovmf.sh` | Locate and validate vendor-enrolled OVMF CODE/VARS pairs under `/usr/share/edk2/x64` |
| `find-ovmf-firmware.sh` | Scan `/usr/share` and map OVMF CODE↔VARS pairs to valid firmware configurations |
| `fix-ovmf-enrollment.sh` | Ensure SB-compatible OVMF VARS exist in `/usr/share/edk2/x64/` (download or generate) |
| `fix-secureboot-now.sh` | Diagnostic/recovery — audit libvirt XML, NVRAM integrity, and SB auto-enrollment failures |
| `win11-secureboot-template.xml` | Windows 11 libvirt domain template — vendor Secure Boot + TPM 2.0 + Hyper-V enlightenments |

---

## Image & host overlay tooling

These bridge the source tree and a running host — they implement parts of the
"repo root **is** the system root" model used during development and packaging.

| Script | Purpose |
|--------|---------|
| `mios-overlay.sh` | Overlay this repo's `usr/`, `etc/`, `var/` onto a host root to "MiOS-ify" a dev/test environment without a full image build |
| `mios-sysext-pack.sh` | Consolidate multiple granular `.sysext` directories into one `mios-accelerator.raw` SquashFS image (works around kernel overlayfs stacking-depth limits at bootc init) |
| `preflight.sh` | Validate the build environment (podman, git, just, disk space, Containerfile presence) before an OCI image build |

---

## Repo & knowledge maintenance

Out-of-image helpers for keeping the source tree, AI manifests, and upstream
tracking current. The generated manifests/snapshots are what let agents and RAG
index this repo — i.e. how the "self-replicating, self-aware" half of MiOS knows
its own layout.

| Script | Purpose |
|--------|---------|
| `generate-ai-manifest.py` | Parse Markdown + metadata blocks into a JSON manifest of the project structure (searchable index for agents) |
| `generate-unified-knowledge.py` | Compile a redacted, compressed `repo-rag-snapshot.json.gz` — a unified semantic knowledge base for RAG |
| `journal-sync.py` | Convert legacy Markdown memory logs into structured JSONL for the MiOS memory system |
| `sync-wiki.py` | Inject current version + RAG-sync timestamps into wiki Markdown metadata |
| `standardize-docs.py` | Enforce uniform legal headers/footers across `specs/` Markdown |
| `ascii-sweep.py` | Normalize non-ASCII typography/emoji in MiOS-owned text to ASCII for consistent rendering |
| `refresh-env.py` | Sync `.ai-environment.json` with editor (`.vscode/settings.json`) preferences |
| `log-to-bootstrap.sh` | Sync AI/RAG artifacts + wiki docs to the `mios-bootstrap` repo for distribution |
| `mios-upstream-monitor.sh` | Track upstream versions (Fedora, bootc, Cockpit, NVIDIA, CrowdSec, Waydroid, …) for available updates |
| `compile-templates.py` | Golden round-trip template validator to verify all templates parse and compile cleanly |

### Template Conformance Gate (Check 46) & Golden Compiler (Check 59)
- **Check 46 (`check_template_conformance`)**: Enforces that all code and documentation files follow the unified template rules (ADR-0011) and carry the appropriate `AI-hint:` metadata header.
- **Check 59 (`check_templates_compilation` / `compile-templates.py`)**: Validates that the templates themselves compile cleanly and are syntactically correct, preventing broken template definitions from slipping into the codebase.

---

## Windows-side helpers

For the Windows build/dev host (the `irm | iex` install path provisions a
`MiOS-DEV` podman machine and drops WSL2/VHDX/ISO/qcow2 artifacts).

| File | Purpose |
|------|---------|
| `windows/Build-MiOS.ps1` | Windows build entry — see [`windows/README-WINDOWS.md`](windows/README-WINDOWS.md) |
| `fix-token-input.ps1` | One-shot fix for token paste-capture in `mios-build-local.ps1` (PowerShell 7.x `Read-Host -MaskInput`) |
| `refresh-flatpak-shortcuts.ps1` | Generate Windows Start-Menu `.lnk` shortcuts for `MiOS-DEV` Flatpak apps (WSLg icon-import workaround) |

---

## Subdirectories

| Path | Contents |
|------|----------|
| [`lib/`](lib/) | Shared helpers used by the toolkit and build scripts (`userenv.sh`, the build/SBOM generators, refactor utilities) |
| [`windows/`](windows/) | Windows build pipeline ([`README-WINDOWS.md`](windows/README-WINDOWS.md)) |
| [`mios-portal-app/`](mios-portal-app/) | MiOS Portal Android app (WebView wrapper for the web portal; see its [`README.md`](mios-portal-app/README.md)) |

---

## How these scripts interact with the bootc image

This is the boundary that keeps the immutable-OS promise honest:

- The **image build** (`../Containerfile` + `../automation/`) produces the OS as
  a single OCI image. That image already carries the AI plane — the inference
  lanes (`mios-llm-light` on the `llm_light` port as the primary llama.cpp
  lane plus the gated heavy GPU lanes), the agent-pipe/MiOS-Hermes
  orchestration, the
  `mios-pgvector` datastore — baked in per **Architectural Law 3 (BOUND-IMAGES)**.
- These **toolkit scripts** run on a host that's *already booted*. They are
  **not copied into the image by default** — they configure or assess the host
  around it.
- If you want one of these tools available *inside* the image (e.g.
  `vfio-verify.sh` pre-installed for diagnostics), add it to the FHS overlay at
  `../usr/local/bin/` and reference it from the relevant `../automation/NN-*.sh`
  step or a `../usr/share/containers/systemd/` Quadlet. Don't symlink from here.

That separation matters because of the build contract these scripts must not
violate. The six **Architectural Laws** govern the image, not this directory, but
the overlay/packing tools here have to respect them:

1. **USR-OVER-ETC** — static config in `/usr/lib/<component>.d/`; `/etc/` is admin-override only.
2. **NO-MKDIR-IN-VAR** — every `/var/` path declared via `usr/lib/tmpfiles.d/*.conf`; never written at build time.
3. **BOUND-IMAGES** — every Quadlet image symlinked into `/usr/lib/bootc/bound-images.d/` and baked in at build time.
4. **BOOTC-CONTAINER-LINT** — final `RUN` of the `Containerfile`; fail = fail the build.
5. **UNIFIED-AI-REDIRECTS** — every agent/tool targets `MIOS_AI_ENDPOINT`; no vendor-hardcoded URLs.
6. **UNPRIVILEGED-QUADLETS** — every Quadlet declares `User=`, `Group=`, `Delegate=yes` (documented exceptions: `mios-ceph`, `mios-k3s`, `mios-forgejo-runner`).

---

## Legacy / out-of-tree

Earlier monolithic provisioners and the standalone Linux-side orchestrator
predate the current `../automation/NN-*.sh` modular pipeline and the
`Justfile` + `../mios-build-local.ps1` build drivers. If a former mega-script
resurfaces in your tree, **do not extend it** — work on the modular replacement
in [`../automation/`](../automation/) instead.

> Guidance for AI agents / System Code in this directory: don't modernize
> working scripts unprompted, and don't rewrite bash into other languages. These
> are intentionally simple host-side shell tools; their stability is the point.

---

See [`../usr/share/doc/mios/reference/licenses.md`](../usr/share/doc/mios/reference/licenses.md)
and [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for upstream ecosystem references
(bootc, BIB, rechunk, cosign, Universal Blue, etc.).
