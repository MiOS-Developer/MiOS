<!-- AI-hint: Research spike deciding what the MiOS core image profile contains, measuring its size budget against a Claude Code cloud VM's container store, and recording the pipeline, preflight and gate work that ships it. -->
<!-- AI-related: usr/share/mios/mios.toml ([profiles.core] [image].core_ref [deployment.cloud].core_min_free_gb), Containerfile, automation/build.sh, automation/88-finalize.sh, .devcontainer/cloud-shell/bootstrap.sh, src/mios-rs/mios-gate/src/profiles.rs, .github/workflows/mios-ci.yml, tests/test-cloud-overlay.py, tests/drift-gate-negatives.sh, usr/share/doc/mios/adr/0025-core-image-profile.md -->
# Technical Investigation & Research Spike: The core image profile for a cloud store that cannot hold the full image

**Spike ID:** `SPIKE-20261121-core-image-profile-cloud`
**Author / Driver:** Claude Code (cloud session, dev-loop)
**Status:** CONCLUDED
**Timebox:** one session (measurements 2026-11-21; written up 2026-11-26)
**Target Completion Date:** 2026-11-26
**Related Epics / Lanes:** ADR-0025 (`usr/share/doc/mios/adr/0025-core-image-profile.md`), -dev-loop `AGENTS.md` operator principle 2026-09-25 ("the same pipeline yields the CORE MiOS components first and the rest degrades open", task #11), MiOS PR #96 (`cloud-session-mios-dev-degrades-open`)

---

## 1. Context & Motivation

A Claude Code cloud session runs on a fixed Ubuntu VM whose podman store measured **57 GB free**
(252 GB disk). The full MiOS image (`[image].ref`, ~23 GB compressed, ~49 GB unpacked) needs
`[deployment.cloud].min_free_gb = 80`, so `bootstrap.sh preflight` fails there by design and
`mios-dev status` says so (PR #96). The operator decided (native question UI, 2026-11-21) that the
remedy is a **core MiOS image profile emitted by the one SSOT-driven pipeline**: not a GCE VM, not an
in-place conversion of the Ubuntu host.

Unverified going in:

- what "core" is, concretely: ADR-0025 declared `[profiles.core]` (sections and phases) but
  marked its size and bake time **unmeasured**, and no CI job has ever built it;
- whether that profile fits the measured allowance with pull headroom;
- whether the declared core is a subset of the full image at all (nothing gated that at the
  package level), and whether a core bake would even pass `build.sh`'s own checks.

Deciding wrong here either ships a "core" that is a second hand-authored image (the defect ADR-0025
exists to remove) or a core that does not fit, so every cloud session keeps failing its preflight.

---

## 2. Research Questions & Hypotheses

| Question ID | Research Question | Hypothesis (Initial Assumption) |
| :--- | :--- | :--- |
| **Q1** | How large is the base image the pipeline builds `FROM`, and the alternatives ADR-0025 Q1 named? | ucore-hci is a few GB; fedora-bootc about half of it. |
| **Q2** | How large is the package closure `[profiles.core].package_sections` resolves to, installed? | Low single-digit GB; the 49 GB of the full image is mostly bound images and weights, not RPMs. |
| **Q3** | Does the declared core carry what a dev session needs (miosd, agent-pipe, resolver, native tools, podman/buildah, toolchains) and nothing of what should degrade open? | Mostly; `[packages.critical]` was suspected to drag desktop packages in. |
| **Q4** | Is the core package set a subset of the full image's, and is that gated? | True today by construction (`full` is `all`), gated nowhere. |
| **Q5** | Does the existing bake machinery emit the core profile unchanged, or does something fail a core bake? | The build arg exists; the postcheck's critical-package list might not. |
| **Q6** | What threshold should the cloud preflight use for the core image, and how does a session learn which image it runs? | Image size plus pull headroom; the preflight must record its choice. |

---

## 3. Experimental Methodology & Prototypes

Everything was measured in this cloud session (Ubuntu 24.04 VM, docker 29.10, HTTPS egress proxy),
against the checkout at `8a91805` (PR #96's head).

- **Registry manifests (Q1):** the amd64 manifest of each image read from its registry's `/v2`
  API; compressed size is the sum of its layers' `size` fields.
  `ghcr.io/mios-developer/mios:latest` is private (403 anonymously), so the SSOT comment's own
  figure (23 GB compressed, 49 GB unpacked) stands for the full image.
- **Package closure (Q2, Q3):** `automation/lib/packages.sh`'s `get_packages` resolved every
  section of `[profiles.core].package_sections` (with `requires_sections`: `base -> mcp`,
  `self-build -> build-toolchain, windows-cross-build`) from the checkout's `mios.toml`.
  The union was installed with `dnf install -y --setopt=install_weak_deps=False
  --skip-unavailable` into `quay.io/fedora/fedora-bootc:44` (the smallest base that resolves the
  same Fedora 44 repositories), in three steps so each part's cost is its own number:
  core without `critical`, then `critical`, then `[packages.devcontainer]` on top. Size is the
  delta of `rpm -qa --qf '%{SIZE}'` and of `du -sxb /`.
- **Section ownership (Q3, Q5):** `tomllib` parse of `[packages]` to find which sections
  declare each `[packages.critical]` package; `grep` of `automation/*.sh` for which phases install
  which sections; `miosd build --sections --profile core` and `--list --profile core` from the
  release build of this tree.
- **Gate (Q4):** `mios-gate profile-integrity` (release build) on the clean tree, then on trees
  with a planted package.

---

## 4. Empirical Findings & Benchmarks

### 4.1 Quantitative Data

| Metric | Measured | Source |
| :--- | :--- | :--- |
| Cloud VM podman store free | **57 GB** (252 GB disk) | PR #96, this VM (`df`) |
| Full image `[image].ref` | ~23 GB compressed, ~49 GB unpacked | SSOT comment; registry private |
| `ghcr.io/ublue-os/ucore-hci:stable-nvidia` (`[image].base`) | **2.51 GB** compressed, 307 layers | manifest |
| `ghcr.io/ublue-os/ucore-hci:stable` | 2.21 GB compressed | manifest |
| `ghcr.io/ublue-os/ucore-minimal:stable` | 1.52 GB compressed | manifest |
| `quay.io/fedora/fedora-bootc:44` (ADR-0025 Q1 candidate) | 1.05 GB compressed; 545 RPMs, 1.71 GB installed | manifest, `rpm -qa` |
| Core sections minus `critical` (159 names, 7 sections) | **664 MiB download, +407 RPMs, +2.94 GB installed** (`du /` +3.2 GB) | dnf on fedora-bootc:44 |
| `[packages.critical]` (13 names) on top | **306 MiB download, +332 RPMs, +0.90 GB installed** | dnf |
| Whole `[profiles.core]` closure (168 names, 8 sections) | **912 MiB download**; 4.65 -> 5.55 GB installed total | dnf |
| `[packages.devcontainer]` (114 names) on top of core | **+8 RPMs, +110 MB** | dnf |
| `miosd build --list --profile core` | 18 phases (of 80) | release miosd |
| `rpmfusion-{free,nonfree}-release-rawhide` (`[packages.repos]`) | no match on Fedora 44 repos | dnf (`--skip-unavailable`) |

Core image estimate (not yet baked): base 2.5 GB compressed (unpacked ~6 GB), plus ~3-4 GB of
RPMs installed, plus the native catalog and the `[build.toolchain]` rustup home (~1.5 GB), plus
the system-files overlay, and **no bound images, GGUF weights, flatpaks or desktop**: about
**11-13 GB unpacked, ~5 GB compressed**. A pull peaks near compressed plus unpacked (~18 GB).

### 4.2 Qualitative Discoveries & Trade-offs

- **F1. The core is small; the full image's bulk is not RPMs.** Every core section together is
  under 1 GiB of downloads and under 4 GB installed on a 1.7 GB base. The 49 GB of the full
  image is the bound-image bake (`57-mios-sys-build.sh`, `mios-bake-group heavy|extra`), model
  weights (`73-model-prep.sh`), GNOME/Hyprland, virt, GPU lanes and flatpaks, all outside
  `[profiles.core].phases`. The Containerfile already gates the bound-image layers on
  `MIOS_BAKE_BOUND_IMAGES` ("ruling Q1: only the full image bakes them").
- **F2. `[packages.critical]` is a verification list that also installs a desktop.** It is
  installed by no phase (`grep` finds no `install_packages* critical`); `build.sh
  _check_critical_packages` reads it to assert presence after the bake. Its 13 names include
  `gnome-shell`, `gdm` (section `gnome`), `libvirt` (`virt`), `cockpit` (`cockpit`): sections
  whose phases (`57-gnome.sh`, `21-virt.sh`) are outside the core. Listed in
  `package_sections`, it is the one core section that costs a desktop (+332 RPMs, +0.9 GB) if a
  phase ever installs it, and as a postcheck it marks a core bake `[MISS]` on those four names.
  `_post_package_health` reports through `_run_stage`, which records a non-fatal post step as a
  WARN, so a core bake completes but its report carries four misses that are not defects of
  the core. **Open item, not changed here:** split `[packages.critical]` by profile or make the
  postcheck skip packages whose providing sections are outside `BUILD_PROFILE_SECTIONS`
  (the data is there: each of those four is owned by exactly one section).
- **F3. The dev userspace is already almost the core.** `[packages.devcontainer]` adds 8 RPMs and
  110 MB over the core closure. The remaining gap between "core" and "what a dev session
  needs" is editor extras, not toolchains; -dev-loop's `FEDORA_PACKAGES` and the hand list stay
  interim projections of this (AGENTS.md environment section).
- **F4. `self-build` pulls `windows-cross-build`** (`mingw64-gcc`, `clang`, `lld`) through
  `requires_sections`, so the core carries the Windows cross target. It is part of "MiOS can
  rebuild itself" and stays; its cost is inside the 2.94 GB above.
- **F5. The subset relation was true by construction and gated nowhere.** `[profiles.full]` is
  `all = true`, so every enabled section is in it; the floor check compared only section and
  phase names. A core section with `enable = false`, or one whose `requires_sections` names a
  disabled section, would have declared packages no image installs, with no finding.
- **F6. Nothing recorded which profile an image is.** `mios-gate image-equivalence` reads
  `MIOS_PROFILES_DEFAULT=` from `/usr/lib/mios/version` ("task_3465 writes both"), but
  `88-finalize.sh` wrote only `MIOS_VERSION`, `MIOS_BASE`, `MIOS_BUILT`; and inside a phase the
  variable holds the resolver's default, not the requested profile (`build.sh` captures
  `_requested_profile` before `common.sh` re-exports the resolved environment for that reason).
- **F7. ADR-0025 Q1 (`fedora-bootc` as the core base) is not needed for the budget.** On
  `ucore-hci:stable-nvidia` the core fits the 57 GB store with more than 3x headroom, and one
  `BASE_IMAGE` for both profiles keeps the Containerfile's `FROM` identical, so the devcontainer
  (`.devcontainer/Containerfile`, `FROM ${MIOS_IMAGE_REF}`) needs no change. Switching the core
  to `fedora-bootc` would save ~1.5 GB compressed and is left as the ADR's open question.

---

## 5. Architectural Implications

- **Contract impact (SSOT, Law 7/8/9):** three new keys, all read by code, none a literal
  elsewhere: `[image].core_ref` (`ghcr.io/mios-developer/mios:core`),
  `[deployment.cloud].core_min_free_gb` (25), `[profiles.core].targets = ["cloud"]`.
  `mios-gen names-registry` regenerated `names.generated.txt` (+3 names); CI passes
  `MIOS_PROFILES_DEFAULT` from the floor profile's name and derives the tag from `core_ref`.
- **Pipeline:** no new Containerfile and no new stage. The core image is the root
  `Containerfile` with `--build-arg MIOS_PROFILES_DEFAULT=<floor> --build-arg
  MIOS_BAKE_BOUND_IMAGES=0`; `build.sh` already filters phases and sections through `miosd`.
  Two lines make the image self-describing: `build.sh` exports `BUILD_PROFILE` (the requested
  profile) and `88-finalize.sh` writes `MIOS_PROFILES_DEFAULT=<profile>` into
  `/usr/lib/mios/version`, the key `image-equivalence` already reads. CI's new `build-core` job
  refuses to publish an image whose record disagrees with the profile it was built as.
- **Gate (Law 8, two-sided):** `mios-gate profile-integrity` now resolves each profile's package
  closure the way `packages.sh` does (`pkgs` + `requires_sections`, `enable = false` excluded
  from what an image installs, a required disabled section an error) and fails when the floor
  declares a package another profile's image never installs. Negatives: Rust integration tests
  (a disabled planted section, a disabled required section) and `tests/drift-gate-negatives.sh`
  (a planted `devloop-planted-pkg` in a disabled section the core names).
- **Cloud preflight:** `bootstrap.sh preflight` chooses `[image].ref` when the store meets
  `min_free_gb`, else `[image].core_ref` when it meets `core_min_free_gb`, else fails naming both
  numbers and both images; the choice is recorded in `$STATE/image.ref` and `pull`, `up`,
  `enter` and `status` read it, so `mios-dev status` names the image and profile a session runs.
  An image already present wins regardless of thresholds. Tests in `tests/test-cloud-overlay.py`
  plant all three outcomes and assert the record and the `status` line.
- **Operational:** the first `build-core` run on main is the first bake of the core profile
  ever; its reported image size replaces the 25 GB estimate in the SSOT (an edit, not code).
  Until it is published, a cloud host under 80 GB gets a core preflight pass and a pull that
  fails for a missing tag, which `status` reports as "the pull failed" with podman's message.

---

## 6. Final Decision & Actionable Next Steps

**Verdict:** ADOPT

### Rationale

The declared `[profiles.core]` is the right core for a dev session: it carries the SSOT and
overlay, miosd and the native catalog (built in the Containerfile's `rust-builder` stage, so
profile-independent), the resolver, podman/buildah (`containers`), the Rust and C toolchains
(`build-toolchain`, `self-build`), Python and Node for agent-pipe and the agent CLIs (`ai`), and
the Quadlet/ports/services phases that render the datastore and service units. Everything that
makes the full image 49 GB (bound images, weights, desktop, GPU lanes, flatpaks, virt, k3s/Ceph)
is outside its phases and degrades open at runtime through the existing `[deployment.cloud]`
overlay and firstboot provisioners. Measured, the whole closure is 912 MiB of RPM downloads on a
2.5 GB base: an estimated 11-13 GB unpacked image against a 57 GB store, so `core_min_free_gb =
25` leaves pull headroom and a 2x margin on the estimate. No second recipe, no new base image.

### Action Items

- [x] SSOT keys, names registry, `[profiles.core].targets` and the comment that said no kind
      gets a subset (`usr/share/mios/mios.toml`).
- [x] `build-core` CI job: bake, bootc-label and profile-record verification, publish and sign
      `[image].core_ref` (`.github/workflows/mios-ci.yml`).
- [x] Profile record in the image (`automation/build.sh`, `automation/88-finalize.sh`).
- [x] Package-subset gate with two-sided controls (`src/mios-rs/mios-gate/src/profiles.rs`,
      `src/mios-rs/mios-gate/tests/profiles.rs`, `tests/drift-gate-negatives.sh`).
- [x] Preflight selection, record and `status` (`.devcontainer/cloud-shell/bootstrap.sh`,
      `tests/test-cloud-overlay.py`).
- [x] Docs: `.devcontainer/cloud-shell/README.md`, -dev-loop `AGENTS.md`.
- [ ] After the first `build-core` run on main: replace the 25 GB estimate with the measured
      image size plus headroom; record the bake time against ADR-0025's `budget_seconds`.
- [ ] F2: make `_check_critical_packages` profile-aware (skip a critical package whose only
      providing sections are outside `BUILD_PROFILE_SECTIONS`) or split `[packages.critical]`.
- [ ] ADR-0025 Q1: decide whether the core moves to `fedora-bootc` (saves ~1.5 GB compressed,
      changes `FROM` per profile); not needed for the budget.
- [ ] `[packages.repos]`'s `rpmfusion-*-release-rawhide` names resolve on no Fedora 44 repo;
      the strict installs would fail on them if that section were ever installed strictly.
