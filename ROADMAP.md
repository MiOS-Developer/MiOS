<!-- AI-hint: MiOS -- Master Roadmap (SINGULAR monolith)
     AI-related: /usr/lib/mios/bake/plan.d/NN-, /etc/mios/blade.d/, /usr/share/mios/artifacts/sbom/bound-images.tsv, /usr/libexec/mios/miosd, /usr/share/mios/vllm/model, /etc/mios/role.conf, /etc/mios/secrets.env, mios-bake-group, mios-bakescratch, mios-llm-heavy -->
# MiOS -- Master Roadmap (SINGULAR monolith)

## What this roadmap is evidence for

MiOS is a **proof of an idea**, not a product chasing users. Everything below is
either evidence for the thesis or it is housekeeping, and the difference should
be visible from the entry.

**The thesis, in four parts that stand or fall together:**

1. **One file defines the whole OS.** Every surface of a running system is a
   projection of `usr/share/mios/mios.toml`, and drift from it is mechanically
   impossible.
2. **Agents operate it natively.** The machine is driven through a typed verb
   surface rather than shell improvisation.
3. **(1) is what makes (2) possible.** An agent can reason about one declarative
   surface; it cannot reason about a thousand hand-edited files. The two claims
   are one claim.
4. **It is one reproducible, immutable artifact.** Rebuildable and rollback-able,
   with provenance from SBOM rather than pinned literals.

**What counts as evidence.** A drift-check that *cannot fail* is not evidence --
it asserts a proof that does not exist, and it is worse than having no check,
because it stops anyone looking. For part 1, the standard is: every surface has a
generator AND a gate that demonstrably goes red. A gate without a negative test
has not been shown to be a gate at all.

**What the reader is meant to receive.** The artifact is *the repository itself*,
small enough that a person can read it and see the idea. That makes size and file
count first-class goals rather than hygiene -- see `[legibility]` in
`mios.toml`, which ratchets them downward the same way the documentation census
does.

**Deployment scope is universal.** Blades, mesh, bare metal, VM, WSL and cloud
are all in scope. Design ahead of hardware is legitimate here; presenting a
*designed* plane as a *demonstrated* one is not.

### Honest state, measured rather than asserted

<!-- ROADMAP_METRICS_START -->
| | Measured | Note |
|---|---:|---|
| Runs on | MiOS-DEV VM / WSL | Bare metal is **untried**; blade/mesh/vfio behaviour is design, not observation. |
| Tracked files | 3,661 | The reading surface. |
| Tracked size | 325 MB | Two vendored assets are most of it. |
| Shell / Python / PowerShell / Rust | 58k / 231k / 31k / 91k lines | Law 14 makes Rust the native tier; PowerShell currently outweighs it 0.3x. |
| Drift checks | 235 | Falsifiability audited per check, not assumed. |
| Units reproducing from SSOT | 15 faithful of 217 | 55 registered as drifting: the largest hole in part 1 of the thesis. |
<!-- ROADMAP_METRICS_END -->

### The four open campaigns

Nothing new starts until these close. Each is tracked as a ratchet with a
measured floor, so "finished" is a number reaching zero rather than a judgement.

1. **Falsifiable gates.** Audit every check for whether an edit exists that makes
   it red. Directly load-bearing: a hollow gate falsifies part 1.
2. **Unit projection.** 39 of 66 units do not regenerate from SSOT. systemd is
   the largest surface, so this is the biggest single gap in "one file defines
   the OS".
3. **Documentation.** 1,724 unharvested narrative comments and 150 stale
   references. Legibility is the deliverable, so this is not tidying.
4. **Projection completeness and code consolidation.** Every file in the image
   must be generated+gated or explicitly declared authored. At the repository
   root and one directory level below it, combine related code into existing
   modular entrypoints where callers and behavior can be preserved. Keep
   distinct FHS destinations, registered build phases, and externally loaded
   contracts separate; measure the tracked-file reduction and update every
   path-based projection when a source file is retired.

**Consolidation evidence (2026-09-30, T-1126):** Three deployment generators and
one dedicated test file were absorbed into the existing `mios-unit-gen` Rust
component. Its CLI and the daemon share one renderer for all seven blade/UKI
outputs; 23 native tests and stale-binary negative controls pass. The broader
root and one-depth consolidation campaign remains active.

**Service configuration follow-up (T-1127):** The Cockpit and FreeIPA producers
also use the same native component, retiring two more scripts. Build phases
share a dispatch helper and the daemon compares both outputs directly. Thirty-one
renderer tests cover payload preservation, input validation, independent roots,
and literal shell-value round trips.

> The one canonical roadmap. Absorbs all former top-level `*-PLAN-*.md` + `concepts/*` planning docs. Workstreams map to `T-*` records in tasks.jsonl.

<!-- ROADMAP_ROLLUP_START -->
### Workstream Status Rollup
- **Done**: 25
- **Active**: 9
- **Proposed**: 5
- **Blocked**: 0
<!-- ROADMAP_ROLLUP_END -->

<!-- ROADMAP_INDEX_START -->
### Workstream Index

**OS-Image & Build**
- `WS-TESTDOC` — Test & documentation harness: drift-gate negative self-tests, shellcheck coverage, bake-smoke ✅
- `WS-BAKEGATE` — Two-gate model: [build.bake] core allow-list + projected bake-plan ✅
- `WS-BLADE` — Universal-core + blade-type ACTIVATION gate (one image, role by flag) ✅
- `WS-MIOSSYS` — MiOS-Sys shared-base consolidation of the sidecar fleet ✅
- `WS-SBOM` — SBOM-not-hardcode: digests/hashes are build-time provenance, never SSOT literals ✅
- `WS-DOCS` — Planning-docs refactor: ADR system + generated index ✅
- `WS-LANG` — Language-per-domain unification — Rust for native tooling, bash demoted to thin glue ✅
- `WS-TEMPLATE` — Compiled file-pattern system — one template per file type + conformance check + Law-14 ✅
- `WS-DEBT` — Technical-debt register — TD-1..TD-8 (shell-mass, version drift, resolver twin, monolith decomposition) (proposed)
- `WS-DIFFCYCLE` — Shutdown Diff Snapshotting, Boot Cycle Accrual & HITL Image Roll-in Pipeline (active)

**AI-Plane & Orchestration**
- `WS-DEPRED` — AI-plane dependency reduction (Hermes→agent-pipe collapse + sidecar consolidations) ✅
- `WS-SCHED` — Engine-level Priority Scheduling and Preemptive Context Switching (active)
- `WS-ORCH` — Structured Deliberation (DCI), Event-Bus Coordination, and LOO Scoring (active)
- `WS-MODELOCI` — Model & Dataset OCI Distribution Architecture (active)

**Deployment & Sovereignty**
- `WS-MDRIVE` — Sovereign "run off M:" deployment (Hyper-V Gen 2 .vhdx + Ceph OSD on M:) (proposed)
- `WS-CAT` — MiOS-Cat unified entry point (one tri-launcher, six verbs, all-platform) ✅
- `WS-CATREPO` — Small MiOS-Repo shadow-config + separate MiOS-Data bulk store (512GB+) + model embedding ✅
- `WS-CATFLAT` — MiOS-Cat tree flatten, de-dup, leave-nothing-behind ✅
- `WS-CONFIG` — Unified config surface: mios.toml ⇄ Portal + configurator + /v1 at :8640/ ✅
- `WS-DEPLOY` — Deployment surface consolidation and bare-metal bootc install ✅

**Storage & Data**
- `WS-DURA` — Durable Brain Persistence, Database SSOT Inversion & Automated Snapshot Timers (active)

**Security & Identity**
- `WS-UPSTREAM` — Upstream watch — primary-source-verified CVE/version tracking for the AI-lane images (active)
- `WS-VFIO` — Whole-Device Discrete GPU Passthrough & Looking Glass B6 Inter-VM Framebuffer (active)

**Desktop & UX**
- `WS-DOTFILES` — SSOT-as-system-dotfiles — one mios.toml projects every dotfile on every platform ✅

**Fleet & Federation**
- `WS-RELTOP` — Release topology: GitHub ≡ Forgejo equal publishers; PUBLISH capacity gate ✅
- `WS-NODE` — Edge Micro-Mesh mios-node 16-Byte Wire Protocol & Dual-Tier Sandboxing (active)
- `WS-DOCGEN` — Generative documentation — AI-hints and comments harvested into the manual, wiki and machine-facing surfaces (active)
- `WS-MINI` — "MiOS-Metal Blades: the bare-metal plane, and a fleet of 2-6 that actually clusters" (planned)
- `WS-NODE-ANDROID` — Android edge micro-node over CDC-NCM USB tethering (proposed)

**Testing & Conformance**
- `WS-TESTDOC` — Testing, drift-gate negative coverage, and documentation integrity ✅

**Global Unification & De-duplication**
- `WS-DEDUP-DISCOVER` — Value duplication reporting, cross-surface scanner, and enforcement gates ✅
- `WS-DEDUP-COLOR` — Color and theme alias collapse to single SSOT representation ✅
- `WS-DEDUP-AIPLANE` — AI model, embed, endpoint, and engine key consolidation ✅
- `WS-DEDUP-NETPATH` — Bind address, browser flags, sub-dirs, and port offset derivation ✅
- `WS-DEDUP-STRUCTURAL` — Resolver double-emission removal and centralized alias table ✅
- `WS-DEDUP-CROSSSURFACE` — SSOT projection across docs, HTML, graph, and build manifests ✅
- `WS-DEDUP-GUP56` — Always-latest float, SBOM pinning, and minimal key library ✅
- `WS-DEDUP-SIGNOFF` — Permanent enforcement, negative coverage, ADR, and campaign signoff ✅
- `WS-HCI · Sovereign Hyper-Converged Infrastructure & Self-Replication Fabric` — WS-HCI · Sovereign Hyper-Converged Infrastructure & Self-Replication Fabric (proposed)
<!-- ROADMAP_INDEX_END -->

<!-- ROADMAP_TOC_START -->
## Table of Contents
- [OS-Image & Build](#os-image-build)
- [AI-Plane & Orchestration](#ai-plane-orchestration)
- [Deployment & Sovereignty](#deployment-sovereignty)
- [Storage & Data](#storage-data)
- [Security & Identity](#security-identity)
- [Desktop & UX](#desktop-ux)
- [Fleet & Federation](#fleet-federation)
- [Testing & Conformance](#testing-conformance)
- [Global Unification & De-duplication](#global-unification-de-duplication)
<!-- ROADMAP_TOC_END -->

---

# OS-Image & Build

## WS-TESTDOC — Test & documentation harness: drift-gate negative self-tests, shellcheck coverage, bake-smoke
<!--
id: WS-TESTDOC
title: Test & documentation harness: drift-gate negative self-tests, shellcheck coverage, bake-smoke
theme: OS-Image & Build
status: done
priority: P1
laws: [7, 8]
ssot_keys: ["build.bake", "deploy"]
adr: [12]
deps: []
acceptance: |
  Drift-gate negative self-tests run in CI; shellcheck error-level coverage lints all script directories; bake-smoke harness uses SSOT components.
-->

**Done this session**: Integrated drift-gate negative self-tests into CI (`.github/workflows/mios-ci.yml`), extended `lint-shell.sh` to error-lint all shell directories across the repo, extracted runtime smoke assertions to `tests/bake-smoke.sh`, and adopted ADR-0012.

### TESTDOC-01 — Drift-gate negative self-test CI integration  **[P1]**
- **What:** Add a dedicated step running `tests/drift-gate-negatives.sh` to the CI workflow in `.github/workflows/mios-ci.yml`.
- **Why:** Ensures drift-check negative assertion tests execute automatically on every PR.
- **Files:** `.github/workflows/mios-ci.yml`, `tests/drift-gate-negatives.sh`.

## WS-BAKEGATE — Two-gate model: `[build.bake]` core allow-list + projected bake-plan (Phase 0 sharded bake ✅)
<!--
id: WS-BAKEGATE
title: Two-gate model: [build.bake] core allow-list + projected bake-plan
theme: OS-Image & Build
status: done
priority: P1
laws: [3, 7, 8, 12]
ssot_keys: ["build.bake.core", "build.bake.groups", "build.bake.group_members"]
adr: [1]
deps: []
acceptance: |
  just drift-gate validates plan.d/*.list; the check fails if a whale leaves core,
  a core member is not fully-qualified, or referenced not in emitted.
-->

**Phase 0 (sharded bake) ✅ DONE this session** — the monolithic `RUN` that pulled ~20 sidecars + both CUDA whales into one `buildah commit` overran disk-constrained runners (`exit 125` / "io: read/write on closed pipe"). Fixed by sharding into one `RUN` per group, heavy-first: `usr/libexec/mios/mios-bake-group` (new; reads `[build].bake_groups`, writes an inner `storage.conf` with `use_hard_links`/`convert_images`/`enable_partial_images`, pulls with `--tmpdir` scratch + ×3 retry, fails LOUD, and records each resolved digest to the SBOM — see WS-SBOM), `usr/share/mios/mios.toml [build].bake_groups` (L8470-8475: `["vllm","sglang","ai","infra","extra"]` — whales first while the store is emptiest), and `Containerfile` L181-190 (five per-group `RUN`s, each `--mount=type=cache,target=/var/tmp/mios-bakescratch`; `ostree container commit` + `bootc container lint` stay last; **never `--squash`**). This moves layer boundaries only, not membership — every image is still baked.

### BAKE-01 — `[build.bake]` core allow-list + `generate-bake-plan.py` projection + drift-check + `.image` whales  **[P1]**
- **What:** Add a dedicated `[build.bake]` SSOT section (a `core` allow-list — the fixed, SSOT-independent membership baked into every image; `groups`/`group_members.*` for shard order) adjacent to `[build]`. Add generator `tools/generate-bake-plan.py` invoked by new numbered step `automation/16-bake-plan.sh` (after `34-render-quadlets.sh`, so `Image=` values are concrete): read `mios.toml` through the shared `usr/lib/mios/mios_toml.py` resolver, resolve each bound Quadlet's fully-qualified `Image=`, emit CORE members **unconditionally** (this one branch is where "core overrides SSOT" is literally implemented) and à-la-carte members **iff** their enable gate resolves true, into ordered `/usr/lib/mios/bake/plan.d/NN-<group>.list`. Encode "engine baked, service disabled" natively with `.image` Quadlets for the whales (`usr/share/containers/systemd/mios-llm-heavy.image` + `mios-llm-heavy-alt.image`), symlinked into `/usr/lib/bootc/bound-images.d/` by extending `automation/01-system-files-overlay.sh` (~L178) to also glob `*.image`. Have `mios-bake-group` consume `plan.d/` (swap out its interim static group tokens).
- **Why:** Phase 0 fixed the crash but the Containerfile still `sed`-scrapes Quadlets and the gate is all-or-nothing (against Law 7 NO-HARDCODE + Law 8 SSOT-PROJECTION). A single `core` list expresses "core is fixed; everything else is enable-gated" in one auditable place and composes with the enable flags MiOS already has, instead of inventing a parallel per-service toggle. `.image` Quadlets are bootc's native encoding of pull-only-no-service (the whales are CORE-baked but VRAM-gated off).
- **Files:** `usr/share/mios/mios.toml` (`[build.bake]`; digest-free `vllm`/`sglang`/`hermes` entries in `[image.sidecars]`), `tools/generate-bake-plan.py` (new), `automation/16-bake-plan.sh` (new), `automation/01-system-files-overlay.sh` (~L178), `automation/98-drift-checks.sh` (new check), `usr/share/containers/systemd/mios-llm-heavy.image` + `mios-llm-heavy-alt.image` (new), `usr/libexec/mios/mios-bake-group`, the `just iso`/BIB path (`--bound-images=stored`).
- **Accept:** `just drift-gate` regenerates `plan.d/*.list` and diffs clean; the check FAILS if a whale falls out of `core`, if a core member is not fully-qualified (no `localhost/`, no short-name), or if `referenced ⊄ emitted`; the Containerfile carries no inline Quadlet `sed`-scraping; a build with all whales in `core` and empty `bake_model` bakes engines-only.
- **Deps:** Phase 0 (done). Interlocks with WS-MIOSSYS (the bake groups collapse toward `sys`/`cuda` built images) and WS-SBOM (digest-free SSOT).

## WS-BLADE — Universal-core + blade-type ACTIVATION gate (one image, role by flag)
<!--
id: WS-BLADE
title: Universal-core + blade-type ACTIVATION gate (one image, role by flag)
theme: OS-Image & Build
status: done
priority: P1
laws: [3, 8]
ssot_keys: ["blade.type", "blade.fallback", "blade.archetypes", "blade.role_aliases", "blade.requires", "blade.seat_side", "blade.soft_ok", "blade.ungated"]
adr: [1]
deps: [WS-BAKEGATE]
acceptance: |
  one universal image specializes into roles cleanly by cmdline token + markers.
-->

### BLADE-01 — `[blade]` archetypes + capability→unit map + declarative `Condition*` activation + deploy-time selection  **[P1]**
- **What:** Add a `[blade]` SSOT: `type` (named archetype: `hybrid`/`compute`/`endpoint`/`controller`/`headless`), `[blade.archetypes]` (each expands to a k8s-style capability label set), and `[blade.requires]` (the "nodeSelector" table mapping each CORE service unit → required capability; a service with no entry is ungated core-of-core that starts everywhere). Demote `usr/libexec/mios/role-apply` from imperative actor (it currently calls `systemctl start`) to a **marker-writing resolver** — expand `type`→capabilities and materialize `/etc/mios/blade.d/<cap>` marker files + `/run/mios/blade.env`; keep autodetect. Generate one `usr/share/mios/dropins/blade-<cap>.conf` per capability (a single `ConditionPathExists=/etc/mios/blade.d/<cap>`) from `[blade.requires]` (Law 8 generator + drift-check) and wire the `automation/48-mios-dropin-fanout.sh` gate table. Deploy-time selection per channel: karg `mios.blade=<type>` (from a generated `usr/lib/bootc/kargs.d/05-mios-blade.toml`) for ISO/bare-metal, Butane/Ignition `kernel_arguments`/marker drops for PXE/Matchbox, Afterburn/cloud-init for hypervisor, `mios blade set|add-capability|status` verb for day-2 (marker `touch` + `daemon-reload`, no reboot). Fold `[profile].role/features` into `[blade]` (thin alias one release, then retire); add `mios-{compute,endpoint,controller}.target` mirroring the existing `Conflicts=`/`AllowIsolate=` pattern; a greenboot check asserts resolved capabilities match the markers.
- **Why:** The sidecar audit flagged code-server, the Guacamole stack, and Matchbox/PXE as "bloat that never runs" — but the operator classifies them CORE. Both are right on different axes: baked on every blade (BAKE gate = core), started only on their blade type (ACTIVATION gate = marker present). A failed `Condition*` is a *clean skip*, not a failure, so a `controller` blade bakes the ~25 GB vLLM image and leaves it condition-skipped at **zero VRAM/boot cost** — identical image, different running set, no variants. Marker files (over raw per-unit kargs) give capability granularity + day-2 mutability without reboot + admin-tier `/etc/mios` override.
- **Files:** `usr/share/mios/mios.toml` (`[blade]`/`[blade.archetypes]`/`[blade.requires]`), `usr/libexec/mios/role-apply`, `usr/share/mios/dropins/blade-<cap>.conf` (generated), `automation/48-mios-dropin-fanout.sh`, `usr/lib/bootc/kargs.d/05-mios-blade.toml` (generated), `usr/lib/systemd/system/mios-{compute,endpoint,controller}.target`, `usr/lib/greenboot/check/required.d/10-mios-role.sh`, the `mios blade` verb.
- **Accept:** one universal image; on a `controller` blade `systemctl status mios-llm-heavy.service` reports condition-skipped with zero VRAM touched, while a `gpu-serving` blade starts it; `mios blade add-capability gpu-serving` lights the unit hot (no reboot); the drop-in generator is drift-gated; `[blades.*]`/`[nodes.*]` fleet-dispatch (Axis B) stays orthogonal to `[blade]` OS-activation (Axis A).
- **Deps:** none hard; complements WS-BAKEGATE (activation vs bake orthogonality) and WS-MIOSSYS (activation `Condition*` unchanged by consolidation).
- **Closed (T-315).** All three named deliverables landed. The karg producer (`tools/native/mios-unit-gen/src/lib.rs` -> `usr/lib/bootc/kargs.d/05-mios-blade.toml`, gate `check_blade_karg`) exists; `[profile]` is retired outright rather than aliased, because measurement showed it was dead on both ends -- no reader, and its only writer emitted `Role` with a capital R; and `role-apply` is demoted to a resolver whose one remaining imperative act is conditional on the role having CHANGED. Two corrections to the plan are recorded in ADR-0016 Decision 4 and T-315: the `systemctl` calls could not simply be deleted (the image bakes `multi-user.target`, so on the first boot after install nothing else reaches the role target, and the default archetype's target requires `graphical.target`), and shipping the karg producer had silently disabled `/etc/mios/role.conf`, its `FEATURES=`, and the hardware fallbacks -- `mios blade set` did nothing and `mios blade add-capability` was erased on the next boot. Resolution is now a five-tier ladder, gated by `check_role_ssot`, with 14 fixture-driven assertions in `tests/test-role-apply-precedence.sh`. Two archetypes (`k3s-master`, `ha-node`) were added because `role-apply` already selected their targets while granting them zero capabilities, and the role targets now form a complete `Conflicts=` graph so day-2 switching actually stops the previous role. See ADR-0016.

## WS-MIOSSYS — MiOS-Sys shared-base consolidation of the sidecar fleet
<!--
id: WS-MIOSSYS
title: MiOS-Sys shared-base consolidation of the sidecar fleet
theme: OS-Image & Build
status: done
priority: P1
laws: [3, 6, 8]
ssot_keys: ["image.sys", "image.cuda", "image.sidecars"]
adr: [2]
deps: [WS-BAKEGATE]
acceptance: |
  collapses container fleet base OS, reducing size down to ~25GB.
-->

### MIOSSYS-01 — Two shared-base images (`mios-sys` + `mios-cuda`) collapse ~18 sidecars via Model A  **[P1]**
- **What:** Replace the ~18-image sidecar fleet (which today shares **zero** base-OS blobs → ~60 GB store) with **two images of one base lineage, both `FROM ${BASE_IMAGE}` (ucore-hci:stable-nvidia)**: `localhost/mios-sys` (CUDA-free glibc base for the Go/Python/Node/native services, ~6-8 GB) and `localhost/mios-cuda` (the shared CUDA/torch/flashinfer L2 whale + sibling `vllm-venv`/`sglang-venv` + `llama-server`, ~15-18 GB). Consolidate via **Model A** (one IMAGE, many CONTAINERS — distinct Quadlets, shared `Image=`, per-service `Exec=`; each keeps its own `User=`/`Group=`/`Delegate=yes` + `Condition*` activation, only the image collapses — the pattern MiOS already ships as `localhost/mios-crawl4ai-slim` etc.). New builder `automation/57-mios-sys-build.sh` (mirrors `52-56-bake-*.sh` + the venv/checksum molds) building into `/usr/lib/containers/storage` with `--layers`; generated `usr/share/mios/sys/Containerfile` + `usr/share/mios/cuda/Containerfile`; `[image.sys]`/`[image.cuda]` SSOT blocks; `MIOS_SYS_IMAGE`/`MIOS_CUDA_IMAGE` threaded through `userenv.sh` + **both** allowlists in `automation/34-render-quadlets.sh` (envsubst arg-string L73 + bash-fallback loop ~L87-127) + asserted in `97-ssot-lint.sh`. The per-member Quadlet delta is a **pure SSOT edit** (repoint `Image=`, add the previously-implicit `Exec=`); the `[build].bake_groups` collapse toward `["sys","cuda","extra"]`. Migration in Waves: **Wave 0** wiring; **Wave 1** Go/static-binary tier (socat[RPM]/adguard/matchbox/forgejo/jaeger/crowdsec — biggest win, lowest risk); **Wave 2** interpreted+native (searxng/open-webui/code-server/valkey/guacd, fold crawl4ai+firecrawl onto shared layers) + k3s/forgejo-runner binaries (privileged activation UNCHANGED); **Wave 3** `mios-cuda` (the ~12-13 GB CUDA/torch byte win) + the DB tier (postgres+pgvector one instance, resolve the PG17-pgvector packaging gap) behind a data-plane smoke test.
- **Why:** The exit-125 root cause is a *big store*, not build-logic; collapsing 18×base → 2×base (and 2.5× duplicated CUDA/torch → one L2) takes the store ~60 GB → ~25 GB — half the win from de-duplicating the vLLM/SGLang whale alone. This makes GitHub-runner-fits-the-bake TRUE (the enabler of WS-RELTOP equality), and simplifies Law 3 (bootc dedups to one/two image IDs). `bootc rollback` reverts OS + baked binaries + Quadlets atomically. The honest lever is single-base-within-the-store (ostree host `/usr` and containers-storage do NOT blob-share), not `additionalimagestores` against host `/usr`.
- **Files:** `usr/share/mios/mios.toml` (`[image.sys]`/`[image.cuda]`/`[image.sidecars]` sys+cuda refs; `[build].bake_groups`→`sys`/`cuda`/`extra`), `automation/57-mios-sys-build.sh` (new), `usr/share/mios/sys/Containerfile` + `usr/share/mios/cuda/Containerfile` (generated), `automation/34-render-quadlets.sh` (both allowlists), `automation/97-ssot-lint.sh`, `automation/14-generate-quadlets.sh`, `usr/libexec/mios/mios-bake-group` (retire the now-superseded `use_hard_links` path), `Containerfile` L181-190, the ~18 `usr/share/containers/systemd/*.container` members.
- **Accept:** the bound-image store drops to ~25 GB (conservative ~27-30) with the largest single commit capped at the ~12 GB CUDA/torch group; `just drift-gate` (`generate-pod-quadlets.py --check`) validates the regenerated `Image=`/`Exec=`; every `User=`/`Group=`/root-exception is byte-identical (Law 6 untouched); a WSL blade still won't start pxe-hub even though its binary is now baked (activation orthogonality holds).
- **Deps:** Locked operator decisions — newest-packages-globally tagged-at-build; ALL core components consolidate; k3s binary consolidated (clustering/HA-compatible, privileged activation unchanged) and Pacemaker/corosync HA is CORE; on-CVE/on-release rebuild cadence (Renovate bumps `MIOS_<X>_VERSION` keys under checksum/GPG verify); Ceph = **KEEP-SEPARATE** (cephadm container-only); `mios-cuda` bake-scope (every blade vs GPU-blade-gated) deferred to Wave 3. Complements WS-BAKEGATE Phase 0 (sharding kept as the free 2× safety margin).

## WS-SBOM — SBOM-not-hardcode: digests/hashes are build-time provenance, never SSOT literals
<!--
id: WS-SBOM
title: SBOM-not-hardcode: digests/hashes are build-time provenance, never SSOT literals
theme: OS-Image & Build
status: done
priority: P2
laws: [7, 8]
ssot_keys: ["image.sidecars"]
adr: [3]
deps: [WS-MIOSSYS]
acceptance: |
  no hand-pinned digests remain in mios.toml; build resolves and records to SBOM.
-->

**✅ DONE (images + model/package/binary hashes):** SSOT (`mios.toml`) refs now carry the TAG intent only (`:latest` = "track newest globally", or a bare version) — a hand-written `@sha256:…` is a hardcode (Law 7). ALL 12 hand-pinned `@sha256` digests were stripped from `mios.toml` (verified: 0 remaining); the 27 rendered Quadlets were regenerated digest-free (verified: 0 `@sha256` in `usr/share/containers/systemd/`; the digest-drift gate is green); and `usr/libexec/mios/mios-bake-group` now resolves each `:latest` at pull time and records `<image>\t<digest>\t<group>` to `/usr/share/mios/artifacts/sbom/bound-images.tsv` (L173-178) — reproducibility comes from the SBOM + baked OCI manifest, not from SSOT digests. This **reverses** the older "pin `@sha256` in `[image.sidecars]` for reproducible builds" convention.

### SBOM-01 — Extend build-time provenance beyond images (model checksums, package version-hashes)  **[P2] ✅ DONE**
- **What:** Apply the same principle to every remaining hand-maintained hash literal: model checksums in `automation/38-llamacpp-prep.sh`, package version-hashes, and the per-app upstream `checksums.txt`/`.asc` verification that WS-MIOSSYS's Wave fetchers introduce — resolved/verified at build and recorded to the SBOM (`automation/90-generate-sbom.sh`), never hand-pinned in `mios.toml`, Quadlets, or scripts.
- **Why:** A hand-pinned hash duplicates SBOM data, can drift from resolved reality, and is a Law-7 hardcode; the SBOM is the single provenance record. Applies beyond images — llama.cpp model checksums, package version-hashes, etc.
- **Files:** `automation/38-llamacpp-prep.sh`, `automation/90-generate-sbom.sh`, the WS-MIOSSYS `automation/NN-*.sh` app fetchers, `usr/share/mios/mios.toml` (version-intent keys only).
- **Accept:** no hand-maintained `@sha256`/checksum literal remains in `mios.toml` or scripts for a runtime-pinned artifact; each resolved hash appears in the SBOM; the digest/checksum drift-checks validate build-resolved values.
- **Deps:** images DONE; interlocks with WS-MIOSSYS (digest-lock the floating `:latest` sources as part of Wave 0) and WS-RELTOP (newest-packages, tagged at build).

## WS-DOCS — Planning-docs refactor: ADR system + lean thematic roadmap + generated index
<!--
id: WS-DOCS
title: Planning-docs refactor: ADR system + generated index
theme: OS-Image & Build
status: done
priority: P1
laws: [7, 8]
ssot_keys: ["meta.mios_version"]
adr: [7]
deps: []
acceptance: |
  every workstream backed by an ADR; generated indexes pass drift checks.
-->
*The meta-workstream that solidifies this whole refactor into cohesive, AI-agent-native docs matching upstream patterns — [MADR](https://adr.github.io/madr/) ADRs · k8s-KEP-style workstream metadata · [Diátaxis](https://diataxis.fr/) doc quadrants · [Keep a Changelog](https://keepachangelog.com/) + SemVer history · `llms.txt`/`AGENTS.md` agent-context. Goal: a future agent starts a workstream from ONE self-contained file, spending tokens on the task not on re-deriving context. Governs the doc tree only; seeds no runtime change. Backing decisions: every WS-* above is now recorded in `usr/share/doc/mios/adr/` (ADR-0001..0006).*

### DOCS-01 — ADR system (`usr/share/doc/mios/adr/`)  **✅ DONE this session**
- **What:** Immutable, numbered, MADR-format Architecture Decision Records, **baked into the image** so a deployed MiOS carries its own *why* (no external wiki). Index + process spec in `adr/README.md` (format, `proposed→accepted→superseded` lifecycle, append-only/never-rewrite rule, the record table, the 13-laws reference). Six foundational ADRs: **0001** two-gate bake/activation · **0002** MiOS-Sys consolidation · **0003** SBOM-not-hardcode · **0004** GitHub≡Forgejo topology · **0005** sovereign run-off-M: · **0006** OpenAI-API-only AI contract. Each frontmatter carries `laws[]`/`ssot_keys[]`/`related_ws[]` cross-linking it to the workstream it seeds.
- **Files:** `usr/share/doc/mios/adr/README.md` + `0001..0006-*.md`.
- **Accept:** every Part-21 WS-* is backed by an accepted ADR (0001→BAKEGATE/BLADE · 0002→MIOSSYS · 0003→SBOM · 0004→RELTOP · 0005→MDRIVE · 0006→DEPRED). **✅ met.**

### DOCS-02 — WS metadata schema + `roadmap-index.py` generator + drift-check  **✅ DONE this session**
- **What:** Give every `WS-*` machine-parseable frontmatter (`id, title, theme, status, priority, laws[], ssot_keys[], adr[], deps[], acceptance`) — KEP-style. Add `tools/roadmap-index.py` that regenerates the top Part/WS index + a status rollup (proposed/active/blocked/done counts) + the Table-of-Contents **from that frontmatter** (fixes the hand-truncated ToC that stops at Part 12), plus a drift-check (`automation/98-drift-checks.sh check_roadmap_index`) that fails if the index is stale or a WS cites a non-existent ADR/law/`ssot_key`. Law-8 SSOT-PROJECTION applied to the planning docs themselves.
- **Files:** `ROADMAP.md` (per-WS frontmatter), `TASKS.md`, new `tools/roadmap-index.py`, `automation/98-drift-checks.sh`.
- **Accept:** `just drift-gate` regenerates the index byte-identically; a WS with a bad `adr:`/`laws:` ref fails the gate; the ToC lists all Parts.
- **Deps:** DOCS-01.

### DOCS-03 — Lean thematic `ROADMAP.md`; archive Parts 1–20 losslessly  **✅ DONE this session**
- **What:** Split the 2,900-line monolith. Keep `ROADMAP.md` as the **current, forward-looking** roadmap grouped by **theme/pillar** (the MiOS "SIGs": OS-Image & Build · AI-Plane & Orchestration · Deployment & Sovereignty · Storage & Data · Security & Identity · Desktop & UX · Fleet & Federation), listing only `proposed/active/blocked` WS. Move the historical/absorbed Parts 1–20 (and every `done` WS) losslessly to `usr/share/doc/mios/roadmap/history/` (dated). Part 21's WS become the seed of the new active roadmap under their themes.
- **Files:** `ROADMAP.md`, `usr/share/doc/mios/roadmap/history/*.md`, `CHANGELOG.md`.
- **Accept:** `ROADMAP.md` is theme-grouped + active-only (~≤600 lines); every archived Part is retrievable under `history/`; the generator's WS rollup total is conserved (nothing lost).
- **Deps:** DOCS-02.

### DOCS-04 — Status-lifecycle retag (honest done-vs-gated) + cross-ref backfill  **✅ DONE this session**
- **What:** Re-tag every WS to the lifecycle with the 2026-06-22 honesty rule formalized: **`done` = active AND live-fired**, never "built-but-gated" (those become `blocked`/`active`). Backfill each WS's `laws[]`/`ssot_keys[]`/`adr[]` so the cross-reference graph (WS↔T↔ADR↔Law↔SSOT-key) is complete + drift-checkable.
- **Files:** `ROADMAP.md`, `TASKS.md`.
- **Accept:** no WS tagged `done` that is gated-off/never-fired; every `done` claim carries a live-evidence line; the cross-ref drift-check (DOCS-02) passes.
- **Deps:** DOCS-02.

### DOCS-05 — Diátaxis reorg of `/usr/share/doc/mios` + `CHANGELOG.md` + agent-context refresh  **✅ DONE this session**
- **What:** Align the whole doc tree to **Diátaxis** quadrants — *tutorial* (day-0/first-boot), *how-to* (`guides/`), *reference* (`api.md`, `mios.toml`, the ports/laws registries), *explanation* (`concepts/`, `adr/`, `roadmap/`). Add a top-level `CHANGELOG.md` (Keep a Changelog + SemVer) fed from `bootc`-version bumps. Refresh `llms.txt` + `AGENTS.md` so an arriving agent is routed in ≤3 hops: current roadmap → ADR index → SSOT → the 13 laws.
- **Files:** `usr/share/doc/mios/**`, `CHANGELOG.md`, `llms.txt`, `AGENTS.md`.
- **Accept:** each doc sits in exactly one Diátaxis quadrant; `llms.txt` resolves an agent to the current-state entry points in ≤3 hops.
- **Deps:** DOCS-03.

## WS-LANG — Language-per-domain unification (Rust for native tooling; bash demoted to thin glue)
<!--
id: WS-LANG
title: Language-per-domain unification — Rust for native tooling, bash demoted to thin glue
status: done
-->
> [!NOTE]
> **Implementation Note (AGY-51):** The native Rust workspace has been scaffolded at `tools/native/` (containing the `mios-version-check` crate). Since no Rust toolchain is present in the host environment, the binary compilation is deferred via `TODO(agy): cargo build`.
>
> **Update (Law 14 landed):** `[laws]` Law 14 **TARGET-LANGUAGES** now MANDATES the language-per-domain contract globally (all platforms), enforced by drift-gate 63 (`check_target_languages`) — no new C#/Batch/Go; existing C# grandfathered in `[laws.target_languages]`. Rust is now **provided as a dependency**, installed during staging via the shared installer contract (`Install-MiosRust` on Windows — winget/rustup-GNU, no MSVC; `mios_ensure_rust` on Fedora — dnf/rustup), so every native component builds on any Windows or Fedora machine. **Next (T-275):** port the first user-facing native component — consolidate the WebView2 wallpaper host + WSLg gui-watch into ONE silent Rust daemon (`wry` WorkerW host + `windows-service`), dropping the Run keys + the terminal/window flash.
>
> **Update (ADR-0021 — consolidation shape decided):** LANG-01's single-`miosd`-multicall design is **reversed**. The native tier is **6–8 separate binaries named by function** — `mios-gate`, `mios-gen`, `mios-resolve`, `mios-serve`, `mios-probe` — over one shared `mios-ssot` crate, with legacy names surviving as `tmpfiles.d` `L+` symlink shims. Separate binaries win on per-binary SELinux labels/capabilities, per-binary SBOM rows and signatures; multicall wins only on image size. Workspace is `src/mios-rs/`; the 14 crates in `tools/native/` are absorbed as modules. Migration is strangler, gate by gate, with byte-identical parity **plus** surviving negative controls, and the script deleted in the same commit that proves parity. Script mass is ratcheted shrink-only via `[legibility]`. See LANG-02..LANG-07 below (→ T-1003, T-1007..T-1012).
-->
theme: OS-Image & Build
status: proposed
priority: P1
laws: [7, 8, 9]
ssot_keys: []
adr: [11]
deps: [WS-DEBT]
acceptance: |
  the correctness-critical orchestration/validation logic (drift-runner, resolve
  core, build driver, verb dispatcher, installer core) moves into one memory-safe
  Rust `miosd` binary invoked by unchanged thin RUNs; the 66 OS-touching steps stay
  shell-thin; the AI plane stays Python; Batch/C# eliminated. Law 8 strengthened.
-->
*Language-per-domain contract (ADR-0011 §2): **Rust** default native tier for tooling/orchestration/validation; **bash** stays thin glue only (the 66 `automation/NN-*.sh` steps, held to the `bash` template); **Python** for the AI plane; **Bun/TS** for the web Portal; TOML SSOT · YAML pipelines · Markdown docs. Go rejected as a second native tier (documented escape hatch only); Batch eliminated; C# `mios-launch.cs` folded into the Rust installer core. All proposed; nothing landed.*

### LANG-01 — Stand up the Rust workspace + port the first fragile bash tool (drift-runner or verb dispatcher)  **[P1]**  (→ T-272)
- **What:** Create the cargo workspace (subcommands `build|drift|verb|resolve|render|cat|scaffold|fmt` → one `miosd` static musl binary) built once in an early cached Containerfile stage and `COPY`'d to `/usr/libexec/mios/miosd`, invoked by **thin RUNs** so the immutable-image contract holds. Port the **first** fragile bash tool — the drift-runner (highest resilience win, lowest coupling; several checks are already Python-in-bash) or the verb dispatcher (removes the 9-verb `eval`-on-agent-args surface) — running old+new side-by-side and diffing to identical before deleting the bash. Collapse the `mios_toml.py` ⇄ `userenv.sh` resolver twin into one crate (`--shell` KEY=VAL emitter + pyo3 face) to end the Law-13 parity drift.
- **Why:** The correctness-critical logic is trapped in fragile bash/batch (44-check drift file in ~3.1k-ln bash, 579-ln build driver, ~150 verb scripts, the resolver twin with no generator binding it). Rust yields a static, single-digit-MB, memory-safe binary cross-compiling to Windows — and one native language serves the "learn from a few files" goal (Law 8) better than two.
- **Files:** the new cargo workspace (location OPEN — `C:\MiOS\src\` is occupied by `mios-launch.cs`+`autounattend/`; candidate `C:\MiOS\tools\native\` or `src\mios-rs\`), `Containerfile` (early Rust stage + `COPY`), `automation/build.sh` (→ thin shim), `automation/98-drift-checks.sh` (checks ported one at a time), `usr/lib/mios/mios_toml.py` + `tools/lib/userenv.sh` (collapse to the crate), the ~150 verb backends.
- **Accept:** `miosd` bakes in a cached stage and is invoked by unchanged thin RUNs; the first ported tool runs byte-identical to the bash it replaces, then the bash is deleted; the resolver twin is one crate with pyo3 + `--shell` faces and `check_userenv_parity` is retired.
- **Deps:** WS-DEBT Phase −1 (shellcheck gate + one version token + one TOML reader unblock the port). ADR-0011. **OPEN QUESTIONS:** native-workspace location; Go escape-hatch; pyo3-vs-subprocess for the AI-plane resolver binding.

*The open questions above are now closed by ADR-0021: the workspace is `src/mios-rs/`; Go stays rejected; the resolver binding is a shared `mios-ssot` crate that JOINS the Law-13 twins rather than replacing them, so bash never depends on a binary it might not have yet.*

### LANG-02 — Workspace foundation: one `src/mios-rs/`, vendored deps, a builder stage, cross-compiled targets  **[P1]**  (→ T-1007)
- **What:** Collapse the two workspaces into `src/mios-rs/`. Absorb the 14 crates under `tools/native/` as modules of the category crates; keep their binary names as `tmpfiles.d` `L+` shims. Delete the orphaned `src/mios-rs/crates/mios-wallpaperd` (a bare `main.rs`, not even a workspace member) in favour of `tools/native/mios-wallpaperd`, which is the real one. `cargo vendor` the dependency tree and commit it with `Cargo.lock` so the bake reaches no network (Law 12). Build in a Containerfile **builder stage** and `COPY` the binaries into the final stage, so no Rust toolchain ships in the image. Targets: `x86_64-unknown-linux-musl` fully static, plus the Windows targets the host-side surface needs.
- **Why:** 20 crates across two roots, one of them duplicated with two different implementations, and only one root was even compiled by the gate. "Static" here means portable across operating systems, not merely self-contained on Fedora — so no binary may depend on the host's glibc or NSS.
- **Files:** `src/mios-rs/Cargo.toml`, `tools/native/*` (absorbed), `src/mios-rs/crates/mios-wallpaperd` (deleted), `src/mios-rs/vendor/`, `Containerfile`, `usr/lib/tmpfiles.d/`, `.gitignore`
- **Accept:** one `cargo build --workspace` from `src/mios-rs/` produces every binary for every declared target with the network off; `tools/native/` holds no crate of its own; every legacy binary name still resolves through a shim; the image contains no cargo/rustc.
- **Deps:** ADR-0021.

### Native architecture validation order (→ T-1147, T-1148, T-1149)

- **Current release:** x86_64 Linux static executables (`x86_64-unknown-linux-musl`) and the full OCI image build, verified inside MiOS-DEV. Keep CLI binaries, desktop applications, services and daemons as separate SSOT roles.
- **Later ARM64 lane:** T-1149 retains `aarch64-unknown-linux-musl` in the SSOT target map. Complete the release catalog, artifact checks, ARM64 runtime smoke checks and bootc image validation before declaring ARM support. The preliminary cross-build was stopped; it does not establish completed support.
- **Evidence boundary:** Cargo's `bin` target kind does not establish static linkage. Reject ELF interpreter and dynamic dependency entries, and check the declared machine architecture. Each architecture needs its own runtime and image evidence.

### LANG-03 — Make `mios-resolver` the ONE Rust reader of `mios.toml`  **[P1]**  (→ T-1008)
- **What:** *(Rewritten after measuring — see ADR-0021 §Decision 5.)* The resolver crate ADR-0021 called for already exists: `tools/native/mios-resolver`, 1,841 lines, with `layers.rs` (tier-major vendor < vendor.d < host < host.d < user < user.d), `merge.rs`, `ports.rs` (the `[ports.categories]` derivation), `aliases.rs` and four emitters — already graded as the third Law-13 twin by `check_resolver_differential_parity`. The work is that **nothing else uses it**. Promote it to the library every crate depends on; retire `src/mios-rs/mios-config`'s parallel loader, whose `load_default()` reads only the vendor file plus `MIOS_*` env — one layer, no `/etc`, no `~/.config`, no `mios.d`, no port derivation — and route `miosd` through the resolver instead. Then ratchet: a shrink-only register of the Rust files still parsing `mios.toml` directly, so a new one fails the gate.
- **Why:** Measured: **11 Rust files parse `mios.toml` without the resolver crate**, and one of them, `mios-config`, is a whole second cascade that silently disagrees with both other twins. Law 13 exists because two implementations drifted; this is a third and a fourth nobody is grading.
- **Files:** `tools/native/mios-resolver/src/lib.rs`, `src/mios-rs/mios-config/` (loader retired), `src/mios-rs/miosd/`, `tools/check-resolver-twin.py`, `usr/share/mios/mios.toml` (the new register), `automation/98-drift-checks.sh`
- **Accept:** `miosd` resolves through `mios-resolver` and its results match the Python twin byte-for-byte on a planted three-layer fixture including the empty-string-does-not-override rule; `mios-config` no longer carries a loader of its own; the ad-hoc-reader count only falls; the twin check fails when any one of the three twins is perturbed.
- **Deps:** none hard — the crate exists today, so this does not wait on LANG-02.

### LANG-04 — `mios-probe`: the first port, and the pattern every later one copies  **[P1]**  (→ T-1003)
- **What:** The `[preflight]` host probe. Greenfield, so no parity risk while the shared crate, the cross-compile, the builder stage and `--format json` are all being invented at once. Establishes the output contract: human text by default with today's exit codes (0 clean / 1 violations / 2 could-not-run), `--format json` emitting an OpenAI-format structured-output schema.
- **Why:** `[preflight]` declared five thresholds that no code read, while the shell probe hardcoded a disk floor of its own (Law 7). The table and the check disagreed and neither knew it. **Done** — see T-1003.
- **Files:** `src/mios-rs/mios-probe/` (new), the shell probe (deleted on parity), `usr/share/mios/mios.toml` `[preflight]`, `Justfile`
- **Accept:** every `[preflight]` threshold changes the verdict when changed in a COPY of the SSOT; `--format json` validates against the declared schema; exit 2 (never 0) on an unreadable input.
- **Deps:** LANG-02, LANG-03.

### LANG-05 — `mios-gate`: the 39 `check-*` scripts and the drift-check body  **[P1]**  (→ T-1009)
- **What:** Port the drift checks one at a time, strangler-style: `automation/98-drift-checks.sh` keeps dispatching, but to the binary. Each port proves **byte-identical output on the real tree** — same violations, same order, same exit code — **and** that every planted negative control still fails *for the planted reason*; the script is deleted in the same commit. The negative controls join `just drift-gate` so the falsifiability half stays continuous.
- **Why:** The 209-check gate is the repo's correctness backbone and it is bash + Python-in-bash, where a syntax error surfaces only when the check runs.
- **Files:** `src/mios-rs/mios-gate/`, `automation/98-drift-checks.sh`, `tools/drift-checks.py`, `tools/check-*.py`, `tools/test_check-*.py`
- **Accept:** for each ported check, old and new emit identical bytes on the current tree; the negative control for that check still fails and names the planted cause; the script is gone from the tree.
- **Deps:** LANG-03.

### LANG-06 — `mios-gen`: the 18 `generate-*` and 4 `render-*` projectors  **[P2]**  (→ T-1010)
- **What:** Port the SSOT projectors. Law 8 already guards each with a regenerate-and-diff check, so parity is verified by machinery that exists.
- **Why:** Generators are the Law-8 surface: every derived file in the tree is only as trustworthy as the projector that writes it.
- **Files:** `src/mios-rs/mios-gen/`, `tools/generate-*.py`, `tools/render-*.py`, `tools/sync-generated.sh`
- **Accept:** each ported generator reproduces its committed output byte-for-byte, and its drift-check still fails on a hand-edited derived file.
- **Deps:** LANG-03.

### LANG-07 — `mios-serve` + the host-side PowerShell surface  **[P2]**  (→ T-1011, T-1012)
- **What:** Daemons into `mios-serve`; the PowerShell host surface cross-compiled, keeping only the paste-able `irm | iex` entry point as PowerShell. 50 files / 24,776 lines, grandfathered by Law 14 for port, not as a licence for more.
- **Why:** PowerShell currently outweighs Rust 1.2x in a repo whose native tier is meant to be Rust.
- **Files:** `src/mios-rs/mios-serve/`, `*.ps1`, `Get-MiOS.ps1` (kept)
- **Accept:** the `mios` verb dispatcher's backends are binaries; `Get-MiOS.ps1` still runs from one paste with no follow-up step; `[legibility].max_ps_lines` falls.
- **Deps:** LANG-02.

## WS-TEMPLATE — Compiled file-pattern system (one template per file type + conformance check + Law-14)
<!--
id: WS-TEMPLATE
title: Compiled file-pattern system — one template per file type + conformance check + Law-14
theme: OS-Image & Build
status: done
priority: P1
laws: [7, 8, 9]
ssot_keys: []
adr: [11]
deps: []
acceptance: |
  ~15 compiled templates (one per authored file type) under usr/share/mios/templates/,
  a `mios new <type>` scaffolder, a golden round-trip compiler, and a
  check_template_conformance drift-check that validates header AND body structure;
  candidate Law 14 (ONE-TEMPLATE-PER-TYPE) registered + enforced (operator-gated).
-->
*A global compiled-template system (ADR-0011 §3) formalizing/extending the AI-hint convention so an agent learns MiOS formatting from a few files. A template = the shared AI-hint header block (produced by the same `mios-ai-tag` engine) + a small per-type body skeleton whose structure is also validated (closing the gap where only the header is checked). Done.*

### TEMPLATE-01 — `usr/share/mios/templates/*.tmpl` (~15) + `mios new <type>` + golden compiler + `check_template_conformance` + candidate Law-14  **[P1]**  (→ T-271)
- **What:** Author ~15 compiled templates (`bash`, `python-tool`, `python-module`, `rust`, `typescript`, `powershell`, `toml-config`, `yaml`, `json-schema`, `markdown-doc`, `adr`, `roadmap`, `systemd-unit`, `quadlet` [generated], `automation-step`) under `usr/share/mios/templates/`, declared in SSOT (`[templates.<type>]`: `match`/`comment`/`required_header`/`required_markers`/`generated`/`scaffold`). Land the scaffolder first as Python `usr/libexec/mios/mios-new` (`mios new <type>`, reusing `mios-ai-tag` for the header, filling canonical fields — next ADR number, next `automation/NN` ordinal, canonical ports — from SSOT, and registering the canonical name), then absorb into `miosd scaffold`. Add a golden round-trip compiler (`tools/compile-templates.py`) and a `check_template_conformance` drift-check (delegating to a Python worker, mirroring `check_hint_coverage → mios-ai-hint-coverage`, degrade-open, soft→hard ratchet). `generated=true` types refuse to scaffold an editable file (scaffold the generator + its `mios.toml` section instead — Law 8 authoritative). **Candidate Law 14 (ONE-TEMPLATE-PER-TYPE):** per ADR-0007 add a `[laws]` row (id 14) + `check_template_conformance` as `enforced_by` — **operator-gated; the `[laws]` edit is deferred for confirmation.**
- **Why:** The AI-hint header + `mios-theme-render` prove the pattern but enforce it piecemeal (header only, no body structure). One compiled, golden-tested template per type makes every file *born from* and *validated against* enforced ground truth — so an agent learns all MiOS formatting from `templates/*.tmpl` + `[templates]`/`[ai_tag]`/`[laws]`. Law 8 mechanically enforced; Law 7/9 via the scaffolder filling canonical fields + registering the name.
- **Files:** `usr/share/mios/templates/*.tmpl` (new, ~15), `usr/share/mios/mios.toml` (`[templates]` schema; candidate `[laws]` id-14 row — operator-gated), `usr/libexec/mios/mios-new` (new; folds into `miosd scaffold`), `usr/libexec/mios/mios-ai-tag` (header machinery, reused), `tools/compile-templates.py` (new), `automation/98-drift-checks.sh` (`check_template_conformance`), `usr/bin/mios`/`Justfile` (`mios new`/`just new`).
- **Accept:** `mios new <type> <name>` produces a conformant file that passes `check_template_conformance` + the golden compiler; a template that can't produce a conformant file fails the build; the header check becomes the header-subset of conformance; Law-14 is proposed with its enforcement wired but the `[laws]` row awaits operator sign-off.
- **Deps:** none hard (Python-first, offline-deterministic); folds into WS-LANG's `miosd` once the Rust workspace exists. ADR-0011. **OPEN QUESTION:** Law-14 confirmation + the next free drift-check number.

## WS-DEBT — Technical-debt register (TD-1..TD-8)
<!--
id: WS-DEBT
title: Technical-debt register — TD-1..TD-8 (shell-mass, version drift, resolver twin, monolith decomposition)
theme: OS-Image & Build
status: proposed
priority: P1
laws: [7, 8, 9]
ssot_keys: []
adr: [11]
deps: []
acceptance: |
  the eight ranked debts (TD-1..TD-8) are tracked with owners + remediation tasks;
  the top three (TD-1 enforce documented conventions, TD-2 collapse version/SSOT to
  one value, TD-3 one TOML reader) land first as near-zero-risk Phase −1 wins that
  unblock the WS-LANG/WS-TEMPLATE work.
-->
*The technical-debt register grounding ADR-0011, re-measured against the live `C:\MiOS` + `C:\mios-bootstrap` trees (server.py=8,961 ln; 44 drift-checks; 3× mios.toml with root=0.2.4 vs SSOT/VERSION=0.3.0; C#/.NET already in-tree at `C:\MiOS\src\mios-launch.cs`). Ranked by severity × reach; the top three are the near-zero-risk Phase −1 prerequisites. All proposed.*

- **TD-1 (Critical) — Fragile-shell mass, conventions documented but not enforced.** ~15k LOC bash across the build chain + ~116 bash verbs; `shellcheck` is only `# shellcheck source=` comments (no CI lint job); 23 verbs lack `set -e`; **9 `eval` on agent-derived args** (injection surface); the 44-check gate is itself fragile bash+grep. → **T-269**.
- **TD-2 (Critical) — Version/SSOT duplication at conflicting values.** 3× `mios.toml` (canonical 10,869 ln vs two ~1.4k-ln roots); `VERSION`/SSOT `mios_version`=0.3.0 but root `C:\MiOS\mios.toml`=**0.2.4**; **37× hardcoded `v0.2.4`** headers; cross-repo divergence of `Get-MiOS.ps1`/`build-mios.ps1`/`mios.toml`/`CLAUDE.md`. Violates Law 9 / ADR-0009. → **T-268**.
- **TD-3 (High) — Hand-rolled parsing of structured formats.** `[packages.*]`/`mios_version`/`enable=` parsed with bespoke `awk`/`grep -m1`/`sed`; JSON via inline `python3 -c`; the **Law-13 resolver twin** (`mios_toml.py` ⇄ `userenv.sh`) must produce byte-identical env with no generator binding them. → folded into WS-LANG (T-272, resolver-core crate).
- **TD-4 (High) — Network-at-build-time, unpinned, WARN-gated-forever.** `55-bake-quickshell.sh`/`56-bake-surfer.sh` clone default branches + build; `build.sh` `NON_FATAL_SCRIPTS` swallows non-zero from ~23 phases. (Remediation: WARN→FAIL on the critical path + pin the bakes; `automation/90-generate-sbom.sh` deliberately out of scope here.)
- **TD-5 (High) — AI-plane decomposition half-finished.** `server.py` is an 8,961-ln god-module; the `mios_pipe/` refactor never reached the 4 largest flat modules incl. `mios_dispatch.py` (the security-critical verb→bash chokepoint); 558 `except Exception` + 9 bare `except:`. → **T-273**.
- **TD-6 (High) — Installer language sprawl + Batch crash-class + quadruplication.** `MiOS-Cat.bat` 1,288 ln / 238 `goto`; MiOS-Cat exists 4×; `build-mios.ps1` 615 KB; the unaccounted C# `mios-launch.cs`. → WS-LANG (installer core → `miosd cat`; retire `.bat`+C#). *(MiOS-Cat files owned by a concurrent agent — not touched here.)*
- **TD-7 (Medium) — Ordinal-filename + substring coupling in the chain.** Duplicate ordinals (three `35-*`, five `38-*`); fatal/non-fatal policy is hand-maintained blobs matched by `grep -qF`. → WS-LANG (build driver owns order/gating/DAG).
- **TD-8 (Low-Med) — Doc/metric over-claim drift + working-tree cruft.** `server.py` "~26k" (actual 8.1k) with no re-derivation gate; ~15 MB gitignored-but-present cruft confuses agents. → the metric-re-derivation gate under WS-TEMPLATE/WS-DEBT.

### DEBT-01 — Phase −1: collapse version/SSOT to one value (TD-2)  **[P1]**  (→ T-268)
- **What:** Strip literal `vX.Y.Z` from all `automation/*.sh` headers; make the two root `mios.toml` (`C:\MiOS\mios.toml` 0.2.4, `C:\mios-bootstrap\mios.toml`) generated projections of the SSOT or delete them; add drift-checks "no literal version in headers" + "root ⊆ SSOT". Near-zero-risk; the highest-reach silent-failure class (a build resolving the wrong copy gets a stale, 7×-smaller manifest).
- **Why:** Every build and every reader can currently bind the wrong version fact. Directly closes a Law 9 / ADR-0009 violation and unblocks the rest.
- **Files:** `C:\MiOS\VERSION`, `C:\MiOS\mios.toml`, `C:\mios-bootstrap\mios.toml`, `usr/share/mios/mios.toml`, all `automation/*.sh` headers, `automation/98-drift-checks.sh` (new checks).
- **Accept:** one authoritative version token; no literal `v0.2.4`/`v0.2.0` in headers; the root `mios.toml` copies are generated-or-deleted and drift-gated; a build can no longer resolve a stale copy.
- **Deps:** none. ADR-0011.

### DEBT-02 — Phase −1: shellcheck CI gate + kill the 9 `eval`-on-agent-args verbs (TD-1)  **[P1]**  (→ T-269)
- **What:** Add a `shellcheck -S warning` CI job over `automation/` + `usr/libexec/mios/` bash; enforce `set -euo pipefail` on the 23 unguarded verbs; audit + eliminate the 9 `eval`-on-agent-derived-args sites (the injection surface on the agent-facing OS-control plane).
- **Why:** The repo documents the conventions (`# shellcheck source=`, `set -e` intent) but never enforces them; the `eval` sites are the highest-severity security debt on the verb chokepoint.
- **Files:** `.github/workflows/mios-ci.yml`, `Justfile`, the 23 unguarded + 9 `eval` verbs under `usr/libexec/mios/`.
- **Accept:** CI fails on a shellcheck warning; the 23 verbs carry `set -euo pipefail`; zero verbs `eval` on agent-derived args.
- **Deps:** none. ADR-0011. Interlocks with WS-LANG (the verb dispatcher port removes the `eval` surface structurally).

### DEBT-03 — Split `mios_dispatch.py` + finish the server.py decomposition (TD-5)  **[P2]**  (→ T-273)
- **What:** Split `mios_dispatch.py` (the security-critical verb→bash chokepoint every verb passes through) out of the 8,961-ln `server.py` monolith into `mios_pipe/`; continue the flat-module extraction (VRAM scheduler, `_db_*`, auth middleware, agent streaming); replace the 9 bare `except:`; add a new gate "no Python file > 800 lines".
- **Why:** The `mios_pipe/` refactor (103 files, 100% hint-tagged) never reached the 4 largest flat modules; the debt is the monolith + the highest-privilege dispatch chokepoint, not the language (Python stays — Law 6).
- **Files:** `usr/lib/mios/agent-pipe/server.py`, `usr/lib/mios/agent-pipe/mios_dispatch.py`, `usr/lib/mios/agent-pipe/mios_pipe/**`, `automation/98-drift-checks.sh` (>800-line gate).
- **Accept:** `mios_dispatch.py` is extracted and live (`check_unwired_modules` confirms); `server.py` shrinks toward a <800-line composition root; no bare `except:`; the line-length gate is green.
- **Deps:** independent track (Python, pure refactor). ADR-0011.

---

## Appendix: Absorbed sources (2026-07-10 consolidation)

ROADMAP.md + tasks.jsonl are now the **singular** planning SSOT. Folded in:
- **9 top-level `*-PLAN-*.md`** (2026-06-14/15) → **Part 17 / T-167–T-177**. Originals archived under `usr/share/doc/mios/archive/absorbed-plans-2026-06/`.
- **~28 `usr/share/doc/mios/concepts/*` docs** → **Part 18 / T-200–T-241** (actionable deltas); the ~24 pure-reference/architecture docs are kept in place and cross-referenced from their Part.
  - [deploy-model.md](file:///c:/MiOS/usr/share/doc/mios/concepts/deploy-model.md) — Mutable Fedora/FHS overlay, immutable bootc, and virtualized VM/Xbox/Windows execution modes.
- Live dGPU heavy-lane diagnosis → **Part 19 / T-178**.
- Retired the old `combine_roadmaps.py` script.

A master index sits at the top of each file; every task carries **Who / What / Where / When / How** + Done-When.

## WS-DIFFCYCLE — Shutdown Diff Snapshotting, Boot Cycle Accrual & HITL Image Roll-in Pipeline
<!--
id: WS-DIFFCYCLE
title: Shutdown Diff Snapshotting, Boot Cycle Accrual & HITL Image Roll-in Pipeline
theme: OS-Image & Build
status: active
priority: P1
laws: [1, 3, 7, 8, 12]
ssot_keys: ["build.bake", "security"]
adr: [1, 8, 14]
deps: [WS-BUILD]
acceptance: |
  System shutdowns snapshot filesystem and git diffs, boot cycles accrue and classify changes, human-in-the-loop auditing permits selective staging, and podman-MiOS-DEV automatically bakes approved diffs into next-boot OCI images with greenboot verification.
-->

### DIFFCYCLE-01 — Systemd shutdown hook capturing pre-poweroff filesystem and git diffs  **[P1]**
- **What:** Record all configuration and operational changes made during a boot cycle before system shutdown or reboot into an immutable snapshot.
- **Why:** Continuous self-evolution requires capturing all live human and agent modifications before power state transitions.
- **Files:** `usr/libexec/mios/mios-diff-snapshot`, `usr/lib/systemd/system-shutdown/mios-diff-snapshot`, `usr/lib/systemd/system/mios-diff-snapshot.service`.
- **Accept:** Diff snapshot is written to `/var/lib/mios/snapshots/boot-diffs/` on every shutdown and reboot.
- **Deps:** none.

### DIFFCYCLE-02 — Boot cycle diff accrual analyzer classifying safe vs high-risk changes on startup  **[P1]**
- **What:** Parse accrued boot diffs on startup, categorize changes against security policies (safe: dotfiles, skills, Wi-Fi profiles; high-risk: root binaries, kernel args, PAM auth), and emit an audit ledger.
- **Why:** Automated image synthesis must differentiate between harmless cosmetic preferences and consequential system security changes.
- **Files:** `usr/libexec/mios/mios-diff-accrue`, `usr/share/mios/mios.toml`.
- **Accept:** Startup analyzer categorizes accrued diffs and prepares structured audit reports for operator review.
- **Deps:** `DIFFCYCLE-01`.

### DIFFCYCLE-03 — Quickshell and CLI interactive diff auditor enabling operator approval of accrued diffs  **[P2]**
- **What:** Present an interactive UI in Quickshell (`DiffReview.qml`) and CLI (`mios diff audit`) allowing the operator to review, approve, or reject accrued diffs before baking.
- **Why:** Human-in-the-loop oversight ensures the operator maintains ultimate sovereignty over what gets baked into the OS image.
- **Files:** `usr/share/mios/shell/components/DiffReview.qml`, `usr/libexec/mios/mios-diff-audit`.
- **Accept:** Operators can visually audit and approve accrued boot diffs through desktop GUI or terminal CLI.
- **Deps:** `DIFFCYCLE-02`.

### DIFFCYCLE-04 — Autonomous image rolling service staging approved diffs for background OCI image synthesis  **[P1]**
- **What:** Commit approved diffs to local git root, trigger `podman-MiOS-DEV` build pipeline in the background with low priority, and stage resulting image via `bootc switch --staged`.
- **Why:** Closing the loop from live operational diffs to rebuilt immutable OCI layers makes MiOS a genuinely self-evolving operating system.
- **Files:** `usr/libexec/mios/mios-diff-bake`, `usr/lib/systemd/system/mios-diff-bake.service`.
- **Accept:** Approved configuration diffs are automatically baked into a new OCI image and staged for next boot.
- **Deps:** `DIFFCYCLE-03`.

### DIFFCYCLE-05 — Greenboot post-bake health gate with automated fallback on diff-induced regressions  **[P1]**
- **What:** Add a greenboot verification script checking the required baked services; on a failed health gate, request `bootc rollback`, quarantine the offending diff, and record which deployment and `/etc` state will be restored.
- **Why:** Automated self-rebuilding needs a recoverable previous deployment. [Upstream bootc rollback](https://bootc.dev/bootc/man/bootc-rollback.8.html) reorders existing deployments; it does not merge current `/etc` edits into the previous one, so recovery must account for configuration as well as image state.
- **Files:** `/etc/greenboot/check/required.d/60-mios-diff-bake-verify.sh`, `usr/lib/greenboot/check/required.d/`.
- **Accept:** A deliberately failing required service queues the previous deployment, records a rollback event, and preserves a separately restorable copy of post-upgrade `/etc` edits; a healthy service leaves the active deployment unchanged. Report uptime from observation, never as a 100% guarantee.
- **Deps:** `DIFFCYCLE-04`.

# AI-Plane & Orchestration

## WS-DEPRED — AI-plane dependency reduction (Hermes→agent-pipe collapse + sidecar consolidations)
<!--
id: WS-DEPRED
title: AI-plane dependency reduction (Hermes→agent-pipe collapse + sidecar consolidations)
theme: AI-Plane & Orchestration
status: done
priority: P2
laws: [5, 7, 8]
ssot_keys: ["ai.endpoint", "hermes.endpoint"]
adr: [6]
deps: [WS-BLADE, WS-MIOSSYS]
acceptance: |
  collapse gateway plane to single port; delete redundant databases/containers.
-->

### DEPRED-01 — Collapse the gateway plane to one `:8640` front door + consolidate sidecars  **[P2]**
- **What:** Collapse MiOS-Hermes (`:8642`) into agent-pipe (`:8640`) — the collapse is already ~70% done (Open WebUI already targets `:8640`, and agent-pipe already owns four of six Hermes responsibilities). Ranked: (1) repoint `MIOS_AI_ENDPOINT` `:8642`→`:8640` in `automation/lib/globals.sh:133` (+ `mios.toml [ai]/[hermes]` endpoints; add `8640` to `[security.nohc_allowlist]`) — one edit redirects every `@`-prompt/CLI client and satisfies Law 5 more cleanly; (2) retire the prefilter (`:8641`) hop (`mios-delegation-prefilter.service`); (3) absorb `gateway_sessions` (port `gateway-agent/session.py` get/save into agent-pipe, opt-in replay); (4) decide the browser/CDP path — expose ChromeDev CDP as MCP `browser_*` verbs and keep `mios-hermes-browser.service` (`:9222`) as a pure executor (recommended) vs retaining one `hermes-worker` browser specialist; (5) retire/alias `mios-gateway-agent.service`. Sidecar consolidations: fold the Guacamole DB into pgvector (delete `mios-guacamole-postgres`, −~430 MB), delete `mios-crowdsec-dashboard` (Quadlet + `[image.sidecars]` pin, −~180 MB), swap cockpit-link's `alpine/socat` container for native `systemd-socket-proxyd`, and replace Open WebUI (`:8033`, ~3.5 GB) with a Quickshell/Hyprland thin SSE `/v1` client to `:8640` (gate OWUI to `edge-endpoint`, then remove).
- **Why:** `:8642` today is a thin shell whose own model is `:8640` and whose MCP verbs call back into `:8640` — a genuinely single front door is cleaner than a secret forwarder. The consolidations trim ~4.1 GB + 3 containers + 2 bound-images without touching the ~47 GB engine floor.
- **Files:** `automation/lib/globals.sh`, `usr/share/mios/mios.toml` (`[ai]`/`[hermes]`/`[security.nohc_allowlist]`), `mios-delegation-prefilter.service`, `usr/lib/mios/gateway-agent/session.py` + agent-pipe `server.py`, `usr/lib/mios/mcp` (browser_* verbs), `mios-hermes-browser.service`, `mios-gateway-agent.service`, `mios-guacamole-postgres.container` + `mios-guacamole.container`, `mios-crowdsec-dashboard.container`, `mios-cockpit-link` unit, a new Quickshell `/v1` panel.
- **Accept:** every front-end resolves `MIOS_AI_ENDPOINT` to `:8640`; `:8641`/`:8642` are retired or thin-aliased; Guacamole runs against a pgvector DB/role; `mios-crowdsec-dashboard` + `mios-guacamole-postgres` are gone; a native SSE client streams `/v1/chat/completions` with model picker + session id + RAG upload.
- **Deps:** Open browser/CDP + `hermes` CLI/Discord decisions per the study's OPEN QUESTIONS; OWUI removal release TBD. Pairs with WS-BLADE (OWUI gated to `edge-endpoint`) and WS-MIOSSYS (fewer images to consolidate).

## WS-SCHED — Engine-level Priority Scheduling and Preemptive Context Switching
<!--
id: WS-SCHED
title: Engine-level Priority Scheduling and Preemptive Context Switching
theme: AI-Plane & Orchestration
status: active
priority: P1
laws: [5, 6, 8]
ssot_keys: ["ai", "dispatch"]
adr: [6]
deps: [WS-DEPRED]
acceptance: |
  Foreground interactive queries bypass background autonomous batches at the tensor engine level, and active turns suspend and resume cleanly via KV slot checkpoints.
-->

### SCHED-04 — Engine-level Priority Scheduling on Heavy Lanes  **[P1]**
- **What:** Pass `--enable-priority-scheduling` in heavy inference lane configurations (`mios-llm-heavy.container`, `mios-llm-heavy-alt.container`, `mios-llm-light.yaml`) and propagate request priority from `PriorityGate` as an HTTP header to vLLM/SGLang.
- **Why:** Prevents foreground interactive queries from being starved behind large background autonomous DAG runs.
- **Files:** `usr/share/containers/systemd/mios-llm-heavy.container`, `usr/share/containers/systemd/mios-llm-heavy-alt.container`, `usr/lib/mios/agent-pipe/server.py`, `usr/lib/mios/agent-pipe/mios_lanes.py`.
- **Accept:** High-priority queries preempt background queue positions and execute with low interactive latency.
- **Deps:** none.

### SCHED-05 — Turn-boundary Preemption & Snapshot-Suspend-Resume  **[P1]**
- **What:** Wire `mios_kvfork.py`, `_CHAT_CANCEL`, and llama.cpp `/slots` save/restore into `server.py` to suspend background tasks and resume from serialized KV caches.
- **Why:** Enables true agentic operating system preemption under mixed foreground/background workloads.
- **Files:** `usr/lib/mios/agent-pipe/server.py`, `usr/lib/mios/agent-pipe/mios_kvfork.py`, `usr/lib/mios/agent-pipe/mios_sched.py`.
- **Accept:** Background tasks suspend upon high-priority arrival and resume from their saved KV slot without losing prior conversation state.
- **Deps:** `SCHED-04`.

## WS-ORCH — Structured Deliberation (DCI), Event-Bus Coordination, and LOO Scoring
<!--
id: WS-ORCH
title: Structured Deliberation (DCI), Event-Bus Coordination, and LOO Scoring
theme: AI-Plane & Orchestration
status: active
priority: P1
laws: [5, 6, 8]
ssot_keys: ["ai", "agents"]
adr: [6]
deps: [WS-DEPRED]
acceptance: |
  Consequential tasks trigger a 4-archetype deliberation council with typed epistemic grammar, event-bus coordination operates over PostgreSQL LISTEN/NOTIFY, and agent contribution is evaluated via IntrospecLOO.
-->

### ORCH-01 — Structured Deliberation with DCI Grammar & Decision Packets  **[P1]**
- **What:** Implement 4-archetype deliberation (Framer, Explorer, Challenger, Integrator) with typed epistemic grammar acts and structured JSON Decision Packet output.
- **Why:** High-consequence operations require structured multi-perspective challenge and consensus rather than unverified single-agent execution.
- **Files:** `usr/lib/mios/agent-pipe/mios_deliberate.py`, `usr/lib/mios/agent-pipe/server.py`, `usr/share/mios/mios.toml`.
- **Accept:** Consequential prompts execute structured deliberation and persist the resulting Decision Packet in `fact_ledger`.
- **Deps:** none.

### ORCH-02 — Document-Mutation Event-Bus Coordination via PostgreSQL LISTEN/NOTIFY  **[P2]**
- **What:** Implement PostgreSQL `LISTEN`/`NOTIFY` on `tasks` and `pending_action` tables to wake background daemons reactively without HTTP polling loops.
- **Why:** Eliminates polling CPU overhead and provides an immutable, auditable event trail in the database.
- **Files:** `usr/share/mios/postgres/schema-init.sql`, `usr/libexec/mios/mios-daemon`, `usr/lib/mios/agent-pipe/server.py`.
- **Accept:** Autonomous tasks trigger reliably via database mutations and log event timestamps with sub-50ms reaction latency.
- **Deps:** none.

### ORCH-03 — Manifest-Guided Progressive-Disclosure Tree Retrieval  **[P2]**
- **What:** Build hierarchical tree retrieval with natural-language `manifest.json` node summaries for top-down pruning before leaf vector similarity search.
- **Why:** Prevents cosine vector space collapse in massive codebases and deep memory hierarchies.
- **Files:** `usr/lib/mios/agent-pipe/mios_manifest_rag.py`, `usr/lib/mios/agent-pipe/server.py`, `usr/share/mios/postgres/schema-init.sql`.
- **Accept:** Hierarchical queries return accurate context with reduced token overhead compared to flat similarity searches.
- **Deps:** none.

### ORCH-04 — IntrospecLOO Marginal Contribution Evaluation for Swarm/Council  **[P2]**
- **What:** Implement Introspective Leave-One-Out (IntrospecLOO) scoring to evaluate agent utility in O(N) single-pass passes and update `peer_reputation`.
- **Why:** Quantifying agent contribution enables dynamic pruning of hallucinating models from future deliberation rounds.
- **Files:** `usr/lib/mios/agent-pipe/mios_reputation.py`, `usr/lib/mios/agent-pipe/server.py`, `usr/share/mios/postgres/schema-init.sql`.
- **Accept:** Completed council sessions record individual agent contribution deltas into `peer_reputation` automatically.
- **Deps:** none.

### ORCH-05 — Identity-Aware Delegation & Progressive Payload Negotiation  **[P2]**
- **What:** Extend A2A protocol and AgentCard schema with reasoning profile metadata and compact semantic-frame JSON payload negotiation.
- **Why:** Lowers token consumption by ~35% in high-frequency inter-agent delegation loops.
- **Files:** `usr/lib/mios/agent-pipe/a2a.py`, `usr/share/mios/ai/v1/agent-card.schema.json`, `usr/lib/mios/agent-pipe/server.py`.
- **Accept:** A2A negotiation selects semantic-frame format between capable peers and falls back gracefully to standard text on legacy endpoints.
- **Deps:** none.

## WS-MODELOCI — Model & Dataset OCI Distribution Architecture (CNCF ModelPack, KitOps ModelKit, Bound-Images Integration)
<!--
id: WS-MODELOCI
title: Model & Dataset OCI Distribution Architecture
theme: AI-Plane & Orchestration
status: active
priority: P1
laws: [2, 3, 5, 12]
ssot_keys: ["finetune.micro", "llamacpp", "ports.llm_light"]
adr: [12]
deps: [WS-DEPRED]
acceptance: |
  Models and fine-tuning datasets package, publish, and ingest via standard OCI v1.1 manifests, CNCF ModelPack specifications, and bootc bound-images.
-->

### MODELOCI-01 — CNCF ModelPack & Kitfile Manifest Integration for MiOS-Micro  **[P1]**
- **What:** Standardize model metadata and layer descriptors in `mios-micro` to comply with CNCF ModelPack (`modelpack/model-spec`) and KitOps `Kitfile` schema v1.0.0.
- **Why:** Replaces ad-hoc model file transfers with versioned, tamper-proof OCI artifacts with discrete layer addresses for model weights (`application/vnd.cncf.model.weight.v1.raw`) and datasets (`application/vnd.cncf.dataset.v1`).
- **Files:** `/workspaces/mios-micro/Kitfile`, `/workspaces/mios-micro/src/mios_micro/convert.py`, `/workspaces/mios-micro/AGENTS.md`.
- **Accept:** `kit pack` or ORAS manifest validation produces an OCI v1.1 manifest with valid `artifactType: application/vnd.cncf.model.manifest.v1+json`.
- **Deps:** none.

### MODELOCI-02 — Multi-Layer OCI Artifact Publishing Pipeline  **[P1]**
- **What:** Automate packaging and publishing of discrete GGUF model layers and SFT datasets to `ghcr.io/mios-dev/mios-micro:1.5b` and local forge registries.
- **Why:** Enables CI/CD pipelines and downstream nodes to pull only needed layers (e.g. weights only or dataset only) with cryptographic digest pinning.
- **Files:** `/workspaces/mios-micro/.github/workflows/package-oci.yml`, `/workspaces/mios-micro/Containerfile`.
- **Accept:** Workflow builds container image and emits OCI artifact layers with SHA256 digests verified in GHCR registry.
- **Deps:** `MODELOCI-01`.

### MODELOCI-03 — Bootc Bound-Image Integration for Resident Inference  **[P1]**
- **What:** Integrate `mios-micro` as a pre-bound container image in `/usr/lib/bootc/bound-images.d/mios-micro.json`, wired directly to `llama-swap` on port key `llm_light`.
- **Why:** Satisfies Law 3 (BOUND-IMAGES) and Law 12 (BAKE-NOT-FETCH), guaranteeing immediate resident availability upon initial host boot without network fetching.
- **Files:** `usr/lib/bootc/bound-images.d/mios-micro.json`, `usr/share/containers/systemd/mios-llm-light.container`, `usr/share/mios/llamacpp/mios-llm-light.yaml`.
- **Accept:** Host image build registers `mios-micro` in `bound-images.tsv` and boots with warm slot responding on port 8500.
- **Deps:** `MODELOCI-02`.

### MODELOCI-04 — Two-Sided Latency & Schema Conformance Gate  **[P2]**
- **What:** Integrate two-sided verification gate in dev-loop evaluating resident inference latency (<250ms target) and negative prompt rejection.
- **Why:** Enforces architectural invariants against prompt injection and model drift in autonomous daemons (`mios-log-watcher`, `mios-cron-director`).
- **Files:** `src/mios_micro/eval.py`, `tests/test_micro_eval.py`, `tools/drift-checks.py`.
- **Accept:** Positive control verifies valid JSON log triage in <250ms; negative control verifies invalid unroutable prompts fail with explicit rejection.
- **Deps:** `MODELOCI-03`.

# Deployment & Sovereignty

## WS-MDRIVE — Sovereign "run off M:" deployment (Hyper-V Gen 2 `.vhdx` + Ceph OSD on M:)
<!--
id: WS-MDRIVE
title: Sovereign "run off M:" deployment (Hyper-V Gen 2 .vhdx + Ceph OSD on M:)
theme: Deployment & Sovereignty
status: proposed
priority: P1
laws: [3, 8]
ssot_keys: ["storage.cephfs.enable"]
adr: [5]
deps: []
acceptance: |
  VM boots off M: vhdx with populated /var/home and single-node Ceph storage.
-->

### MDRIVE-01 — Boot the universal image as a Hyper-V Gen 2 VM off `M:\MiOS-images\` with sovereign Ceph storage  **[P1]**
- **What:** Deploy the universal image as a **Hyper-V Generation 2 VM booting a `.vhdx` on `M:\MiOS-images\`**, cut from the OCI image by `bootc install`/bootc-image-builder (`just vhdx` at `Justfile:217` already runs the `bootc install`-class installer that **factory-populates `/var` + `/var/home`**, installs the bootloader, and honors kargs — the direct fix for the raw `wsl --import` failure). Add a **`vhdx-m` Justfile recipe** (after `vhdx:`) that cuts + drops the artifact on M: and prints the `New-VM` one-liner, and a new **`C:\mios-bootstrap\deploy-mios-hyperv-m.ps1`** that loads the tar, cuts the vhdx if missing, `New-VM -Generation 2` off M: with `Set-VMFirmware -SecureBootTemplate MicrosoftUEFICertificateAuthority`, attaches the Ceph OSD vhdx, adds `netsh interface portproxy` for `:8640`, and does the DDA/GPU-P block. **Sovereign storage** = a 2nd dynamic `.vhdx` on M: (`mios-ceph-osd.vhdx`) attached as the single-node Ceph OSD block device backing `/var/home` (`var-home.mount` is `Type=ceph`), so home + container data persist in a file on M: and survive a root-vhdx rebuild. This requires relaxing **`ConditionVirtualization=no`** on `ceph-bootstrap.service` + `mios-ceph-bootstrap.service` to a **config-flag gate** (`[storage.cephfs].enable` / a `/run/mios/ceph-enabled` flag) instead of a hardware gate; the local **20 GiB `/var/home` ext4 partition** already carved by `config/artifacts/vhdx.toml` is the automatic `nofail`+`ConditionPathExists` fallback when Ceph is down (no new code). dGPU via **DDA** (recommended — the 9950X3D iGPU carries the Windows desktop so the whole discrete GPU goes to MiOS) or **GPU-P** (shared, keeps Windows on the dGPU). WSL2 `--import-in-place` is an explicit **disposable preview only** (still no populated `/var` → not the sovereign target; needs a WSL preset masking the bootc-host units).
- **Why:** Confirmed root cause — a bootc image bakes **nothing** into `/var` (Law 2: `/var` is *declared* via tmpfiles, *materialized* at install/first-boot); only the installer populates it. A raw `podman export`/`wsl --import` of a bare rootfs deadlocks on the `bootc-*`/`ostree-*`/composefs host units (no deployment substrate) and has no `/var/home`. Hyper-V Gen 2 is the only candidate that is simultaneously a true bootc host (real UEFI/GPT + populated `/var/home` + honored kargs + working `bootc upgrade`/`rollback`), runs in place off M: as a single dynamically-expanding file, is native to Windows 11 Pro, and can feed the heavy lanes the real dGPU. QEMU-WHPX has no PCI passthrough (heavy lane → CPU); WSL runs the MS kernel and bypasses the bootloader.
- **Files:** `Justfile` (new `vhdx-m` recipe, ~L217), `config/artifacts/vhdx.toml` (unchanged; optionally bump root 150→200 GiB), `usr/lib/systemd/system/ceph-bootstrap.service` + `mios-ceph-bootstrap.service` (`ConditionVirtualization=no` → config gate), `usr/libexec/mios/ceph-bootstrap.sh` (add OSD-on-`/dev/sdb` + fs creation), `usr/share/mios/mios.toml [storage.cephfs].enable`, `usr/lib/systemd/system-preset/95-mios-wsl.preset` (WSL fast-shim, optional), `C:\mios-bootstrap\deploy-mios-hyperv-m.ps1` (new).
- **Accept:** a MiOS Gen 2 VM boots off `M:\MiOS-images\mios-0.3.0.vhdx` with a populated `/var/home`, `bootc status` healthy, and `curl http://localhost:8640/v1/models` answering from Windows via portproxy; with the OSD vhdx attached + `[storage.cephfs].enable=true`, `findmnt /var/home` reports `type ceph` and survives a root-vhdx rebuild; `nvidia-smi` + a heavy-lane inference call succeed in-guest; `bootc upgrade`/`rollback` work.
- **Deps:** re-establish a Linux podman once (BIB/`bootc install` need it); operator decisions on GPU policy (DDA vs GPU-P), Ceph-now-vs-later, OSD sizing, and the `ConditionVirtualization` scope (prefer the flag-file gate over a blanket removal so transient CI VMs don't auto-enable Ceph). VM/operator-gated.

## WS-CAT — MiOS-Cat unified entry point (one tri-launcher, six verbs, all-platform)
<!--
id: WS-CAT
title: MiOS-Cat unified entry point (one tri-launcher, six verbs, all-platform)
theme: Deployment & Sovereignty
status: done
priority: P1
laws: [1, 7, 8, 9, 12]
ssot_keys: ["editions", "colors"]
adr: [8]
deps: [WS-MDRIVE]
acceptance: |
  one tri-launcher (.ps1/.sh/.bat) exposes stage/install/build/update/provision/manual
  identically across shells; every legacy entry point becomes a verb back-end;
  `cat install` is headless-identical; the [cat] SSOT block resolves (no dangling
  drivepath/medicatver/cache_path reads).
-->

### CAT-01..04 — Flatten + single-owner · verb dispatch + tri-launcher parity · `[cat]` SSOT + dangling-read fix · fold the web one-liners  **[P1]**  (→ T-256..T-259)
- **What:** Make MiOS-Cat the ONE front door for all of MiOS. (1) `mios-bootstrap` owns MiOS-Cat canonically at `cat/` — `git mv` the deep `src\autounattend\medicat_installer\` nest up to `cat\`, **delete** the byte-identical `C:\MiOS` copy (Law 1 / two-repo rule). (2) One tri-launcher (`MiOS-Cat.{ps1,sh,bat}`) dispatches six SSOT-projected verbs — **stage · install · build · update · provision · manual** — under which every existing entry point (`Get-MiOS.ps1` irm|iex, `bootstrap.{ps1,sh}` curl, the UUP/autounattend ISO pipeline, `mios-kickstart.cfg`, the `just` build) becomes a *sub-system*, not a peer; port the advanced `.bat` logic (MiOS-Repo staging, WinPE DISM, self-update) into the canonical `.ps1` (Law 9 parity), reduce `.bat` to the WinPE/cmd shim. (3) Add a `[cat]` block to `mios.toml` (`drivepath`/`medicatver`/`cache_path`/`repo_partition`/`data_partition`/`models`) and repoint MiOS-Cat to resolve the real 597 KB SSOT — fixing the dangling reads that silently hardcode today (Law 7/8). (4) Collapse `Get-MiOS.ps1`/`bootstrap.{ps1,sh}`/`install.*` bodies to thin `cat install` shims and wire the bidirectional `irm⇄curl` handoff (same front door, two shells).
- **Why:** ~6 parallel install/deploy entry points, none authoritative; the MiOS-Cat tree buried 3–5 levels deep and byte-identical across both repos (a Law-1 problem — `C:\MiOS/usr/` *is* `/usr`); and `drivepath`/`medicatver`/`cache_path` reference keys absent from any `mios.toml` → silent hardcoded fallbacks. One owner, one verb vocabulary, one SSOT is the Law 9 closure on the entry surface.
- **Files:** `C:\mios-bootstrap\cat\MiOS-Cat.{ps1,sh,bat}` + `cat\lib\`, `C:\mios-bootstrap\{Get-MiOS,bootstrap,install}.ps1` + `bootstrap.sh`, `C:\MiOS\src\autounattend\medicat_installer\**` (delete), `usr/share/mios/mios.toml` (`[cat]`), `automation/98-drift-checks.sh` (new `[cat]`/`[colors]`-resolve check).
- **Accept:** one MiOS-Cat home; `C:\MiOS` free of the installer (`diff` finds no cross-repo dup); `cat install` is headless-identical across `.ps1`/`.sh`/`.bat`; `irm …/cat | iex` and `curl …/cat.sh | sh` reach the same verb set; no MiOS-Cat value is hardcoded that has an SSOT home.
- **Deps:** `WS-MDRIVE` supplies the deployment mechanism (`cat install --target hyperv|wsl` delegates to `just vhdx-m` / `deploy-mios-hyperv-m.ps1` / `just wsl2` verbatim — MiOS-Cat only fronts it). ADR-0008.

## WS-CATREPO — Small MiOS-Repo shadow-config + separate MiOS-Data bulk store (512GB+) + model embedding
<!--
id: WS-CATREPO
title: Small MiOS-Repo shadow-config + separate MiOS-Data bulk store (512GB+) + model embedding
theme: Deployment & Sovereignty
status: done
priority: P1
laws: [8, 12]
ssot_keys: ["ai.bake_models"]
adr: [8]
deps: [WS-CAT, WS-BAKEGATE]
acceptance: |
  a SMALL always-present MiOS-Repo partition carries the shadow-config brain
  (mios.toml + mios.html + Portal + MiOS-Cat + a small repos-clone); a SEPARATE
  MiOS-Data store (created only on 512GB+ disks) carries the bulk (OCI tar +
  just-all artifacts + model weights + dnf/flatpak/pip mirrors); `cat provision`
  brings a host up fully-featured with ZERO network (Law 12).
-->

### CATREPO-01 — Small MiOS-Repo shadow-config partition (always) + degrade-open fallback  **[P1]**  (→ T-260)
- **What:** `cat stage` populates a SMALL always-present `MiOS-Repo` partition (P3, target ~≤16 GB) with the **shadow-config brain**: `mios.toml` (SSOT), `mios.html` (configurator), the MiOS Portal assets, a self-contained MiOS-Cat copy, and a **small repos-clone** (config/source — not the binary payload). This is the offline embodiment of the ADR-0009 shareable-link surface. Each payload class is degrade-open (online `git clone` → offline copy from `MiOS-Repo/repos/`). **Fix the kickstart path mismatch:** the `.bat` stages repos to one path but `mios-kickstart.cfg` looks under another — align both to a canonical `MiOS-Repo/repos/`. Ventoy-bootable ISOs/WIMs stay on the Ventoy data partition.
- **Why:** The brain (config + Portal + Cat + a small clone) is tiny and belongs on every stick; forcing the 78 GB bulk onto the same "MiOS-Repo" tier bloated every USB and broke the small case. Keeping MiOS-Repo small + always-present is what makes "a link + a stick + a computer" honest.
- **Files:** `cat\MiOS-Cat.{ps1,sh}` (`cat stage`), `usr/share/mios/mios.toml` (`[cat].repo_partition`), `mios-kickstart.cfg` (`%post` repo path), the `MiOS-Repo/` layout.
- **Accept:** a small stick carries the shadow-config brain and a fully offline bare-metal kickstart install succeeds from `MiOS-Repo/repos/`.
- **Deps:** `WS-CAT` (verb engine + `[cat]` SSOT). ADR-0008.

### LOGBOOT-01 — harden+complete the RAG-artifact bootstrap logger  **[P2] ✅ DONE**  (→ T-287)
- **What:** `tools/log-to-bootstrap.sh` publishes the AI-RAG artifacts + wiki to the MiOS-bootstrap repo. Its generated `README.md`/`manifest.json` still advertised the **purged** ollama runtime + the `localhost:11434` native API. Retargeted to the MiOS **`/v1`** lane (Hermes gateway `:8642`, OpenAI-compatible; `mios-llm-light`/`heavy`), added `--retry` to the example call, and switched knowledge-graph injection to `jq --rawfile` (valid JSON). Endpoint is the SSOT `MIOS_AI_ENDPOINT`, not a hardcode.
- **Follow-on:** AGY-103 hardens the PRODUCER of `artifacts/ai-rag/` to match (SSOT-driven, no purged-runtime refs).
- **Files:** `tools/log-to-bootstrap.sh`. Cross-ref: legacy-purge → /v1-only; ai-endpoint-canonical.

### CATREPO-FIX — repos wrongly staged onto MiOS-Data instead of MiOS-Repo  **[P1]**  (→ T-274)
- **Bug (live):** `cat stage` lands the `repos/` clone on the **MiOS-Data** partition — but MiOS-Data is for caches / models / user-DBs / dependencies ONLY. The config/source repos clone belongs on the small always-present **MiOS-Repo** partition (this is the concrete manifestation of the CATREPO-01 kickstart path mismatch).
- **Fix:** correct the staging path in `cat/MiOS-Cat.{bat,ps1}` so `repos/` → `MiOS-Repo/repos/`; assert nothing repo-class writes to MiOS-Data; align `mios-kickstart.cfg`.
- **Accept:** a fresh `cat stage` places `repos/` on MiOS-Repo, MiOS-Data holds only bulk/cache classes.

### CATREPO-02..04 — Separate MiOS-Data bulk store (512GB+) + model embedding + `cat provision` (Law 12) + offline mirrors  **[P1]**  (→ T-261..T-263)
- **What:** On disks ≥ 512 GB (`Get-Disk` gate), `cat stage` creates a **separate** `MiOS-Data` store carrying the **bulk**: the ~78 GB `podman save` OCI tar (offline `podman load`), the `just all` disk artifacts (`raw/iso/qcow2/vhdx/wsl2`, incl. the ADR-0005 VHDX), the `mios.toml`-defined **model weights**, and the offline `dnf`/`flatpak`/`pip` **mirrors** (`reposync`+`createrepo_c`, `flatpak create-usb`, `pip download`). Read the model SSOT keys (`[ai].bake_models` L5744/L6116, `[ai.vllm].bake_model` L6724, `[ai.sglang].bake_model` L6742), fetch+checksum into `MiOS-Data/models/` (never invent — Law 8; resolved-not-hardcoded — WS-SBOM pattern). `cat provision` copies weights to `/usr/share/mios/vllm/model` (+ the GGUF dir) offline. `cat update` re-pulls both stores when online and re-stamps `manifest.json`.
- **Why:** Law 12 (BAKE-NOT-FETCH) realized as offline provisioning — the OCI image bakes engines only (weights a-la-carte per ADR-0001/0002); MiOS-Data is the offline weight+package store so a host deploys fully-featured with zero network — the sovereignty guarantee. Separating bulk from the always-present brain means a 512 GB+ stick is fully offline while a small stick still deploys (degrade-open).
- **Files:** `cat\MiOS-Cat.{ps1,sh}` (`cat stage`/`cat provision`/`cat update`), `usr/share/mios/mios.toml` (`[cat].data_partition`, `[cat].models` → `[ai].bake_models`), `MiOS-Data/{images,models,dnf,flatpak,pip}/`, `automation/38-llamacpp-prep.sh` (checksum pattern).
- **Accept:** on 512 GB+, offline `podman load` + `bootc switch` from USB works; a deployed host's heavy lane comes up with zero network (the `config.json` weight gate present); an offline build/first-boot resolves all packages from USB.
- **Deps:** `WS-CAT`; `WS-BAKEGATE` (the bake-plan that defines what artifacts exist). Model-redistribution licensing is an OPEN QUESTION (ADR-0008 Consequences) — if disallowed, MiOS-Data stores a fetch manifest + checksums instead of weights. ADR-0008.

## WS-CATFLAT — MiOS-Cat tree flatten, de-dup, leave-nothing-behind
<!--
id: WS-CATFLAT
title: MiOS-Cat tree flatten, de-dup, leave-nothing-behind
theme: Deployment & Sovereignty
status: done
priority: P2
laws: [1, 8, 9]
ssot_keys: []
adr: [8]
deps: [WS-CAT]
acceptance: |
  cat/ tracks source only; ~6 MB+ tracked cruft gone; no cross-repo double-track;
  a generated ADR root breadcrumb reaches the ADR index in ≤2 hops; drift-gate green.
-->

### CATFLAT-01..03 — Dead-weight purge + leave-nothing-behind · ADR root breadcrumb · seed-copy consolidation  **[P2]**  (→ T-264..T-266)
- **What:** (1) Delete tracked cruft (`Get-MiOS.ps1.bom-bak`, `commit*.patch`, `temp{,2}.txt`, `scratch.ps1`) after verifying no live consumer (flatten-campaign guardrail); drop committed Ventoy/7z/MediCat binaries (downloaded artifacts, not source) and keep the fetch-on-demand logic; fold MediCat i18n down to MiOS strings. (2) Generate an ADR root breadcrumb — `C:\MiOS\ADR.md` + `cat\ADR-0008.md` — from SSOT (Law 8, drift-checked), linked from `llms.txt`/`AGENTS.md`, so the record is discoverable near each repo root without moving the baked ADRs out from `/usr` (Law 1). (3) Resolve the `mios.toml` seed-copy question — the 63 KB `C:\MiOS\mios.toml` + 68 KB `C:\mios-bootstrap\mios.toml` seeds vs the 597 KB SSOT (which is canonical, which is generated); document + regenerate seeds; MiOS-Cat reads only the SSOT.
- **Why:** Leave-nothing-behind: `cat/` should track source only; the ADRs stay baked (Law 1) but need a root-discoverable pointer; the seed copies are the root cause of the dangling-read bug WS-CAT fixes and must be pinned down.
- **Files:** `C:\mios-bootstrap\cat\**` (cruft purge, i18n), `C:\MiOS\ADR.md` (generated), `cat\ADR-0008.md` (generated), `llms.txt`, `AGENTS.md`, `C:\MiOS\mios.toml` + `C:\mios-bootstrap\mios.toml` (seed provenance), the ADR breadcrumb generator (`roadmap-index.py`-class, Law 8).
- **Accept:** `cat/` tracks source only (~6 MB+ cruft gone); an agent reaches the ADR index from repo root in ≤2 hops; one documented SSOT + generated seeds; drift-gate green.
- **Deps:** `WS-CAT` (single-owner flatten must land first). ADR-0008.

## WS-CONFIG — Unified config surface: mios.toml ⇄ Portal + configurator + /v1 at :8640/
<!--
id: WS-CONFIG
title: Unified config surface: mios.toml ⇄ Portal + configurator + /v1 at :8640/
theme: Deployment & Sovereignty
status: done
priority: P1
laws: [5, 7, 8]
ssot_keys: ["portal", "ports.agent_pipe"]
adr: [9]
deps: []
acceptance: |
  mios.html configurator folds INTO the MiOS Portal, served at GET / on :8640
  alongside the OpenAI /v1 API; the Portal is the shareable-link front door; every
  deployment type/config reads/writes mios.toml through it (Laws 7/8).
-->

### CONFIG-01 — Fold `mios.html` into the MiOS Portal at `:8640/` (one web + API front door)  **[P1]**  (→ T-267)
- **What:** Fold the standalone configurator `mios.html` (`usr/share/mios/configurator/`) INTO the MiOS Portal as a configurator *view*, so `mios.toml` + `mios.html` + the Portal are ONE config surface served at `:8640/` by agent-pipe: `GET /` serves the Portal (with the configurator folded in) and `/v1/*` serves the OpenAI API — the SAME single front door (the ADR-0006 convergence). Wire read/write of `mios.toml` from the configurator view through `mios_portal.py`; the Portal "needs config too" resolves as *it is configured through the surface it is*. The Portal (`:8640/`, or its `[portal].public_host` hosted equivalent) is the shareable web LINK that bootstraps the whole pipeline (open → configure → deploy); the USB MiOS-Repo shadow-config (WS-CATREPO / ADR-0008) is its offline embodiment.
- **Why:** Two web surfaces (a standalone configurator + the Portal) meant two things to serve/secure and an ambiguous "where do I configure MiOS?" The Portal is already served by agent-pipe at `GET /` on `:8640`, and ADR-0006 already collapsed the API plane to `/v1` at the same port — folding the configurator in is the natural closure. Everything projecting from one `mios.toml` (Law 8) is what makes "one config surface" honest; addressed by key never literal (Law 7).
- **Files:** `usr/lib/mios/agent-pipe/mios_portal.py` (configurator view + `mios.toml` read/write), `usr/lib/mios/agent-pipe/server.py` (`GET /` + `/v1/*` one door), `usr/share/mios/portal/` (absorb the configurator UI), `usr/share/mios/configurator/mios.html` (folded in / retired standalone), `usr/share/mios/mios.toml [portal]` (L220), `tools/mios-portal-app/` (Android client → same `:8640/`).
- **Accept:** the configurator is a view within the Portal at `:8640/`; `GET /` (Portal) and `/v1/*` (OpenAI API) share the one door; every deployment type's config reads/writes `mios.toml` through the surface; the shareable link + the USB are the same surface online and offline.
- **Deps:** none hard (the Portal + `:8640` `/v1` already exist). Converges with `WS-DEPRED` (the single `:8640` front-door collapse) and is governed by ADR-0007. ADR-0009.

# Storage & Data

## WS-DURA — Durable Brain Persistence, Database SSOT Inversion & Automated Snapshot Timers
<!--
id: WS-DURA
title: Durable Brain Persistence, Database SSOT Inversion & Automated Snapshot Timers
theme: Storage & Data
status: active
priority: P1
laws: [1, 7, 8]
ssot_keys: ["storage", "security"]
adr: [9, 10]
deps: []
acceptance: |
  PostgreSQL+pgvector acts as live SSOT with lossless TOML export, automated zstd-compressed backups execute daily via systemd timers, and CephFS provisions isolated user homes.
-->

### DURA-01 — Authority Inversion: PostgreSQL+pgvector as Live SSOT  **[P1]**
- **What:** Complete `materialize-config-toml.py` to generate `mios.toml` from the database `config_kv` and `verbs` tables with atomic replace and drift gating.
- **Why:** Enables transactional runtime updates by agents while maintaining human-readable TOML exports.
- **Files:** `usr/libexec/mios/materialize-config-toml.py`, `usr/libexec/mios/seed-db-config.py`, `automation/98-drift-checks.sh`.
- **Accept:** The database serves as the runtime authority and `materialize-config-toml.py` produces an identical, valid `mios.toml` artifact.
- **Deps:** none.

### DURA-02 — Automated PostgreSQL+pgvector Backup & zstd Snapshot Timer  **[P1]**
- **What:** Create systemd backup service and timer running `pg_dump` with `zstd -T0` compression and 7-day retention in `/var/lib/mios/backups/pgvector/`.
- **Why:** Protects mutable agent memory, knowledge graphs, and task ledgers against data loss during OS upgrades.
- **Files:** `usr/lib/systemd/system/mios-backup-pgvector.service`, `usr/lib/systemd/system/mios-backup-pgvector.timer`, `usr/libexec/mios/mios-backup-pgvector`.
- **Accept:** Daily automated database backups execute via systemd timer and older snapshots are rotated according to retention policy.
- **Deps:** none.

### DURA-03 — Multi-tenant CephFS User Directory & CephX Auto-Provisioning  **[P2]**
- **What:** Update `mios-cephfs-provision` to generate per-user CephX authentication keys scoped to `/home/<username>` subvolumes with PAM auto-mounting.
- **Why:** Guarantees cryptographic tenant isolation on shared distributed CephFS storage pools.
- **Files:** `usr/libexec/mios/mios-cephfs-provision`, `usr/lib/pam.d/mios-cephfs-auth`, `usr/share/mios/mios.toml`.
- **Accept:** User logins automatically provision and mount secure CephFS home volumes with dedicated cryptographic keys.
- **Deps:** none.

# Security & Identity

## WS-UPSTREAM — Upstream watch: verified CVE/version tracking for the AI-lane images
<!--
id: WS-UPSTREAM
title: Upstream watch — primary-source-verified CVE/version tracking for the AI-lane images
theme: Security & Identity
status: active
priority: P1
laws: [3, 7, 8, 12]
ssot_keys: ["image.sidecars"]
adr: [3, 12]
deps: [WS-SBOM]
acceptance: |
  Every AI-lane image ref is either float-latest with an SBOM digest record or an
  exact pin with an automated bump path; every CVE claim about the stack carries a
  primary-source badge in a dated reference report; and the stale doc surfaces that
  corrupted external research are re-rendered from SSOT.
-->

*Born from the 2026-08 verification pass (`usr/share/doc/mios/reference/upstream-gaps-2026-08.md`): a batch of externally-generated deep-research reports fabricated versions, registries and CVE fix-lines, and the repo's own drifted docs fed the fabrication. This workstream makes upstream tracking verifiable — primary sources, badges, SSOT-grounded framing (`usr/share/mios/prompts/upstream-research.xml.md`) — and closes the pin/probe gaps the pass surfaced. Companion upstream docs: `usr/share/doc/mios/upstream/inference-engines.md`, `usr/share/doc/mios/upstream/pgvector.md`.*

### UPSTREAM-01 — float the last hand-pinned image refs  **[P1] ✅ DONE**  (→ T-288)
- **What:** Float pgvector `0.8.3-pg17` → `pg17` (the family tag) so it resolves to newest on every build, and bump the one ref that cannot float, k3s, to `v1.36.3-k3s1`.
- **Why:** ADR-0012 float-latest: a hand-pinned version rots and CVE response becomes a manual bump instead of a rebuild. k3s publishes no usable channel tag *and* `06-enable-external-repos.sh` parses the Kubernetes repo minor out of its version-shaped tag, so it stays version-shaped and is bot-bumped (UPSTREAM-03) rather than hand-maintained.
- **Files:** `usr/share/mios/mios.toml`, `usr/lib/mios/bake/plan.d/04-extra.list`, `usr/share/containers/systemd/mios-{pgvector,k3s}.container`, generated projections via `tools/sync-generated.sh`.
- **Accept:** no hand-pinned pgvector version anywhere (the `check_version_ssot` literal exemption for it is withdrawn); the `-pg17` suffix documented as a data-dir compatibility constraint, not a pin.
- **Deps:** none.

### UPSTREAM-02 — derive `mios-resolve-latest` refs from SSOT  **[P1] ✅ DONE**  (→ T-289)
- **What:** Replaced the script's hand-mirrored ref array (four entries had drifted) with refs read from `mios.toml [image.sidecars]` through the `mios_toml` resolver, and fixed the provenance defects found alongside it: a fabricated fallback digest (the SHA-256 of empty input) recorded when resolution failed, a literal `$` in the SBOM date column, and a hardcoded `latest` tag on every row.
- **Why:** A drifted mirror feeds wrong images into the SBOM record — the exact failure the SSOT exists to prevent.
- **Files:** `usr/libexec/mios/mios-resolve-latest`, `automation/98-drift-checks.sh` (`check_resolver_ssot_refs`), `tests/drift-gate-negatives.sh`, `usr/share/mios/reference/drift-gate-index.tsv`.
- **Accept:** the resolver set is derived (21 registry refs, was a 9-entry mirror); `check_resolver_ssot_refs` fails on any re-introduced ref literal and is covered by a negative test.
- **Deps:** none.

### UPSTREAM-03 — Renovate manager for the exact-pinned sidecar entries  **[P2] ✅ DONE**  (→ T-290)
- **What:** `customManagers` regex keeping the `[image.sidecars]` refs that CANNOT float current (k3s), with `pinDigests: false` so Renovate cannot write the hand-pinned `@sha256` that ADR-0003 forbids.
- **Why:** Renovate only managed the Containerfile base-image ARG. Everything with a moving tag upstream now floats (UPSTREAM-01); k3s has no usable channel tag and its version-shaped tag feeds the Kubernetes-repo-minor derivation, so a bot has to bump it or it rots — it was already a patch behind.
- **Files:** `renovate.json`.
- **Accept:** the manager matches only version-shaped refs in the SSOT and leaves every float (`:latest`/`:main`/`pgNN`/major-only/`localhost/`) unmanaged; mios.toml bumps require review.
- **Deps:** none.

### UPSTREAM-04 — runtime kernel-lockdown boot probe  **[P2] ✅ DONE**  (→ T-291)
- **What:** Warn-tier greenboot check asserting the booted kernel reports `[integrity]`, degrade-open on kernels without the lockdown LSM.
- **Why:** `lockdown=integrity` was projection-checked at build but never verified on the running host.
- **Files:** `usr/lib/greenboot/check/wanted.d/31-kernel-lockdown.sh`, `.gitignore` whitelist.
- **Accept:** probe passes on a lockdown-enabled boot, warns on mismatch, silent on WSL2.
- **Deps:** none.

### UPSTREAM-05 — PG-major migration for mios-pgvector  **[P2] ✅ DONE**  (→ T-292)
- **What:** `mios-pgvector-major-upgrade.service` (ordered `Before=mios-pgvector.service`) makes the `pgNN` major movable, and the ref moved to `pg18`. On a `PG_VERSION`/image-major mismatch it dumps the old cluster **with an image of the OLD major** into `[pgvector].restore_sql` — bind-mounted at `docker-entrypoint-initdb.d/20-mios-restore.sql`, so the new cluster replays it after `10-mios-schema.sql` (`pg_dump --clean --if-exists` makes that ordering idempotent) — then stashes the old data dir as `<data_dir>.pg<old>.<timestamp>`.
- **Why:** PG 18 is current upstream, and the upstream images ship one major's binaries each, so in-place `pg_upgrade` is unavailable; without this guard, advancing the suffix strands the entire agent datastore.
- **Files:** `usr/libexec/mios/mios-pgvector-major-upgrade`, `usr/lib/systemd/system/mios-pgvector-major-upgrade.service`, `usr/lib/tmpfiles.d/mios-pgvector.conf`, `usr/share/mios/mios.toml` (`[pgvector].restore_sql`, the Quadlet `Volume`), `usr/share/containers/systemd/mios-pgvector.container`.
- **Accept:** non-destructive on every failure path — absent old-major image, failed dump, empty dump and downgrade all leave the data dir untouched and exit 0, so pgvector stops on Postgres's own major-mismatch error rather than initialising an empty cluster; the old cluster is stashed, never deleted.
- **Deps:** none (the daily `mios-pgvector-backup` timer already ships as a second safety net).

### UPSTREAM-06 — re-render the stale doc port/lane tables from SSOT  **[P2]**  (→ T-293)
- **What:** Regenerate the port/lane tables in README.md, CLAUDE.md, `api.md`, GEMINI.md and SECURITY.md from `[ports]` + the Quadlet lane mapping.
- **Why:** Three mutually inconsistent port schemes circulate; external research inherits the stale ones and "corrects" real values back to wrong ones.
- **Files:** `README.md`, `CLAUDE.md`, `GEMINI.md`, `SECURITY.md`, `usr/share/doc/mios/reference/api.md`, a renderer under `tools/`.
- **Accept:** one port scheme (the SSOT's) across every doc, guarded by a projection drift-check.
- **Deps:** none.

### Research intake — verify upstream boundaries before implementation

The 2026-09-29 external architecture report is an investigation input, not a MiOS implementation record. Attach primary-source evidence and a MiOS runtime observation to the existing work before promoting a design claim to **done**:

| Existing work | Required evidence and correction |
|---|---|
| `VFIO-01`, `DIFFCYCLE-05` → `T-1119` | Prove signed UKI boot and OSTree composefs `signed` or `verity` mode separately; MOK module trust is a different boundary. Check the booted deployment, measured composefs digest, `/var` persistence, and `/etc` after upgrade **and rollback** on a real bootc host. WSL cannot prove this chain. See [OSTree composefs](https://ostreedev.github.io/ostree/composefs/) and [bootc rollback](https://bootc.dev/bootc/man/bootc-rollback.8.html). |
| `WS-BAKEGATE`, `WS-MIOSSYS` → `T-1120` | For each bound `.image` or `.container`, verify the `/usr/lib/bootc/bound-images.d/` symlink, image availability at install and after upgrade/rollback, and per-unit `GlobalArgs=--storage-opt=additionalimagestore=/usr/lib/bootc/storage`. Do not enable that store globally for floating images. See [bootc bound images](https://bootc.dev/bootc/logically-bound-images.html). |
| `VFIO-02` → `T-1121` | Check every device in the assigned IOMMU group before binding and measure the guest GPU and Looking Glass display paths separately. The report's DMA-BUF zero-copy and sub-frame latency claims remain unverified for MiOS; graphics transport does not imply guest CUDA. See [kernel VFIO](https://docs.kernel.org/driver-api/vfio.html) and [Looking Glass](https://github.com/gnif/LookingGlass). |
| `WS-SCHED`, `WS-DEPRED` → `T-1122` | Observe llama-swap's real queue, health, unload, and TTL behavior with the configured model map and an OOM or hung child. Do not assume a fixed five-second kill sequence or durable KV-cache paging from the report. See the [upstream llama-swap interface](https://github.com/mostlygeek/llama-swap/blob/main/README.md). |
| `WS-DEPRED`, `WS-TESTDOC` → `T-1123` | Verify MCP stdio and current Streamable HTTP negotiation, request metadata, cancellation, and legacy compatibility through the SDK. HTTP+SSE is deprecated; SSE as a response body is still valid. Keep MCP input-schema requirements distinct from the stricter OpenAI tool schema used at the `/v1` boundary. See the [2026-07-28 transport specification](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx). |
| `WS-DURA` → `T-1124`; `WS-LANG` → `T-1125` | Size HNSW and WAL settings from MiOS's actual PostgreSQL image and measured memory/write load; do not copy the report's PostgreSQL 16 or `wal_level=minimal` example into the current pg18 deployment. For keyring-to-child credentials, prove the intended file descriptor survives `execve`; a descriptor left `FD_CLOEXEC` cannot be read by that child. See [pgvector index guidance](https://github.com/pgvector/pgvector/blob/master/README.md) and [kernel key retention](https://docs.kernel.org/security/keys/core.html). |

## WS-VFIO — Whole-Device Discrete GPU Passthrough & Looking Glass B6 Inter-VM Framebuffer
<!--
id: WS-VFIO
title: Whole-Device Discrete GPU Passthrough & Looking Glass B6 Inter-VM Framebuffer
theme: Security & Identity
status: active
priority: P1
laws: [1, 3, 8]
ssot_keys: ["security"]
adr: [16]
deps: []
acceptance: |
  Discrete GPU binds to vfio-pci cleanly on boot, Looking Glass connects with sub-frame display latency, UKI boot chain is verified, and credentials are encrypted in 0600 files.
-->

### VFIO-01 — UKI and fs-verity Boot Chain End-to-End Verification  **[P1]**
- **What:** Build a signed UKI with baked kargs and initramfs via `automation/40-composefs-verity.sh`, configure OSTree composefs verification explicitly, and add greenboot checks for each active trust boundary. Treat MOK out-of-tree module signing separately.
- **Why:** A signed UKI alone does not demonstrate that the deployed OSTree commit or backing objects are verified; the booted configuration and digest must be measured.
- **Files:** `automation/40-composefs-verity.sh`, `automation/42-uki-build.sh`, `usr/lib/ostree/prepare-root.conf`, `/etc/greenboot/check/required.d/52-mios-composefs.sh`.
- **Accept:** On a real bootc host, report the actual Secure Boot/UKI result, composefs mode, commit signature and fs-verity digest result, plus a negative corrupted-object or wrong-key control that fails at the expected boundary. WSL results are labeled as substrate-only observations.
- **Deps:** none.

### VFIO-02 — Full-Device Discrete GPU Passthrough & Looking Glass B6 Inter-VM Framebuffer  **[P1]**
- **What:** Implement `mios-vfio-setup` to bind discrete GPU to `vfio-pci` and configure `/dev/kvmfr0` shared memory for Looking Glass B6.
- **Why:** Whole-device passthrough is the supported path for guest CUDA when the host keeps its physical GPU driver unbound; Looking Glass is a separate display path whose latency must be measured.
- **Files:** `usr/libexec/mios/mios-vfio-setup`, `usr/lib/systemd/system/mios-vfio-setup.service`, `usr/share/mios/mios.toml`.
- **Accept:** All functions in the GPU's IOMMU group bind safely, the guest demonstrates compute on the assigned device, and Looking Glass capture/display latency is measured on the target hardware. Record reset failures and recovery; do not infer CUDA from a VirtIO graphics transport.
- **Deps:** none.

### VFIO-03 — Quadlet Credential Hardening & 0600 secrets.env Rotation Service  **[P1]**
- **What:** Implement `mios-secret-rotate` to generate random passwords into `/etc/mios/secrets.env` (0600) and inject via `EnvironmentFile`.
- **Why:** Purges hardcoded plaintext credentials from world-readable container unit definitions.
- **Files:** `usr/libexec/mios/mios-secret-rotate`, `usr/lib/systemd/system/mios-secret-init.service`, `usr/share/containers/systemd/*.container`.
- **Accept:** All credentials are dynamically generated, stored in `0600` files, and `check_credential_literals` reports 0 grandfathered violations.
- **Deps:** none.

# Desktop & UX

## WS-DOTFILES — SSOT-as-system-dotfiles (projection registry + engine + both-sides gate)
<!--
id: WS-DOTFILES
title: SSOT-as-system-dotfiles — one mios.toml projects every dotfile on every platform
theme: Desktop & UX
status: done
priority: P1
laws: [1, 7, 8, 9, 13]
ssot_keys: ["dotfiles.registry", "colors", "theme", "appearance", "terminal", "identity", "btop", "shell", "ssh"]
adr: [10]
deps: []
acceptance: |
  Every declared dotfile on Linux/Windows/WSL is render-generated from mios.toml
  via [dotfiles.registry.*] + mios-dotfiles-render and check-gated both sides;
  no dotfile is hand-typed; the operator overlay projects the same on every
  deployment. Extends (not replaces) the LANDED mios-theme-render + check-25
  palette/btop projection.
-->
*Generalizes the LANDED palette+btop projection into `mios.toml` = the cross-platform system dotfiles-as-code (ADR-0010). The proof of concept is already in the tree: `usr/libexec/mios/mios-theme-render` gained a **settings-surface** concept this session, `[btop]` (~60 keys) projects the whole `etc/btop/btop.conf` unified Linux+Windows, and drift-check 25 (`check_theme_projection`) auto-extended and is proven green. Everything below (the SSOT `[dotfiles.registry.*]` map, the live-HOME `apply` verb, the new `[shell]`/`[editor]`/`[git]`/`[ssh]` domains) is proposed.*

### DOTFILES-01 — `[dotfiles.registry.*]` map + `mios-dotfiles-render` (arbitrary-key, format-aware merge, live-HOME `apply`) + both-sides gate  **[P1]**  (→ T-270)
- **What:** Promote the hardcoded Python `SURFACES` dict into an SSOT-authored `[dotfiles.registry.<surface>]` map (per-platform `target.<os>`; `kind` = template/json-merge/registry/command/skip; `format`; `sources`; `platforms`; `condition`). Transcribe the existing color+btop surfaces first (pure refactor, check 25 stays green), then fork `mios-theme-render` → `mios-dotfiles-render`: registry from `mios_toml.load_merged()`, `@MIOS:<section>.<key>@` tokens, format-aware `merge` that preserves foreign keys (WT/VS Code `settings.json` never clobbered), per-platform target resolution, and a new **`apply`/`diff` verb writing to live HOME** (`~/.config`, `%USERPROFILE%`, `%LOCALAPPDATA%`). Add the new domains `[shell]`/`[editor]`/`[git]`(→`[identity]`, Law 9)/`[ssh]`(`secret_ref`). Generalize `check_theme_projection` (check 25) → `check_dotfiles_projection` over the full registry; add the Windows runtime half `Test-MiOSProjection`; collapse the scattered `Install-MiOS*` bodies into thin registry-driven `Sync-MiOSDotfiles` calls; add a `mios dotfiles apply/diff/drift` verb (`[verbs.dotfiles_*]`).
- **Why:** MiOS proved operator-defined-SSOT-projection for the palette + `[btop]` end-to-end, but every other dotfile (WT `settings.json`, `.gitconfig`, VS Code, shell rc, GTK, ssh) is hand-maintained or projected imperatively, and the `SURFACES` tuples can't express a per-platform target. This is the literal completion of the pattern (Law 8), layered (Law 1/13), one canonical name per surface (Law 9), no literals (Law 7).
- **Files:** `usr/share/mios/mios.toml` (`[dotfiles.registry.*]`, `[shell]`/`[editor]`/`[git]`/`[ssh]`), `usr/libexec/mios/mios-theme-render` (reference; forks to `mios-dotfiles-render`), `usr/libexec/mios/mios-sync-theme`, `usr/lib/mios/mios_toml.py` + `tools/lib/userenv.sh`, `automation/98-drift-checks.sh` (check 25 → `check_dotfiles_projection`), `Get-MiOS.ps1` (`Sync-MiOSDotfiles`/`Test-MiOSProjection`), `usr/bin/mios`.
- **Accept:** the color+btop surfaces are registry-driven with check 25 green; a `[theme].opacity` edit projects to Linux CSS + the WT `json-merge` block + the WSL bridge with foreign keys intact and both gates pass; `mios dotfiles apply` writes live HOME; no `Install-MiOS*` value is hand-typed that has an SSOT home.
- **Deps:** none hard; interlocks with WS-CONFIG (the Portal edits the `[dotfiles.registry.*]` map) and ADR-0005/0008 (the overlay carries across deployments). Secrets (`secret_ref`) and a deployment-type enum for `condition` are OPEN QUESTIONS (ADR-0010).

# Fleet & Federation

## WS-RELTOP — Release topology: GitHub ≡ Forgejo equal publishers; `PUBLISH` capacity gate
<!--
id: WS-RELTOP
title: Release topology: GitHub ≡ Forgejo equal publishers; PUBLISH capacity gate
theme: Fleet & Federation
status: done
priority: P2
laws: [7, 8]
ssot_keys: ["build.curl_trigger_fallback"]
adr: [4]
deps: [WS-MIOSSYS]
acceptance: |
  GitHub and Forgejo are equal publishers; PUBLISH capacity gates the bake on standard runners.
-->

**✅ DONE this session (for CI):** GitHub Actions (`.github/workflows/mios-ci.yml`) and the self-hosted Forgejo runner (`.forgejo/workflows/build-mios.yml`) are declared EQUAL, bit-for-bit build/publish environments (both `podman build`, identical OCI manifests) — neither subordinate. Build is LOCAL-first (MiOS-DEV, 707 GB, bakes the full fleet). `mios-ci.yml` carries a workflow-level `PUBLISH: 'false'` env (L38) — a **capacity** gate, NOT a demotion: a standard `ubuntu-24.04` runner (~66 GB `/mnt`) cannot hold the ~60 GB baked store (one `buildah commit` → exit 125), so GitHub build+lint VALIDATES only while the 707 GB Forgejo runner (and the local build) bake. `PUBLISH` gates the `MIOS_BAKE_BOUND_IMAGES` build-arg (L243) + the rechunk/push/cosign steps (L270+); flip to `'true'` once a runner can hold the bake — or, decisively, after WS-MIOSSYS shrinks the store to ~25 GB so a standard GitHub runner bakes+publishes as a full equal.

### RELTOP-01 — Wire "default-to-GHCR-if-creds-else-local/Forgejo" registry selection into the build driver  **✅ DONE this session**
- **What:** Implement the registry-selection logic that both workflows currently hardcode as `ghcr`: default to GitHub/GHCR push+pull when credentials are present, else the local/Forgejo registry. Locate it in the build driver / `install.env` credential detection so both CI environments and the local build resolve the registry the same way.
- **Why:** The topology directive says registry preference is credential-driven, but `mios-ci.yml`/`build-mios.yml` currently hardcode GHCR; the selection belongs in one shared place, not duplicated per workflow.
- **Files:** `.github/workflows/mios-ci.yml`, `.forgejo/workflows/build-mios.yml`, the build driver (`automation/build.sh` / `install.env` credential detection).

## WS-NODE — Edge Micro-Mesh mios-node 16-Byte Wire Protocol & Dual-Tier Sandboxing
<!--
id: WS-NODE
title: Edge Micro-Mesh mios-node 16-Byte Wire Protocol & Dual-Tier Sandboxing
theme: Fleet & Federation
status: active
priority: P1
laws: [1, 5, 8, 12]
ssot_keys: ["nodes", "blade"]
adr: [16, 17]
deps: [WS-BLADE]
acceptance: |
  Distributed micro-nodes communicate over a deterministic 16-byte binary wire header with CRC32 integrity, execute tasks in Wasm sandboxes, and synchronize state via CRDTs.
-->

### NODE-01 — 16-Byte Fixed Binary Wire Protocol Implementation in mios-node  **[P1]**
- **What:** Implement fixed 16-byte binary wire framing (Magic 0x4D49, Version 0x01, Opcode, NodeID, PayloadLen, CRC32) with zero-copy codec in `mios-node`.
- **Why:** Provides ultra-compact, low-latency message framing for embedded nodes and microVMs.
- **Files:** `src/mios-rs/crates/mios-node/src/protocol.rs`, `src/mios-rs/crates/mios-node/src/main.rs`, `src/mios-rs/Cargo.toml`.
- **Accept:** Codec encodes and decodes all seven message opcodes with full CRC32 validation.
- **Deps:** none.

### NODE-02 — Tier-1 Wasm Sandbox Runtime with mios_sys_* Host Imports  **[P1]**
- **What:** Integrate `wasmtime` runtime with strict fuel limits, 64MB memory caps, and scoped `mios_sys_*` host function imports.
- **Why:** Allows safe execution of offloaded compute tasks on untrusted edge nodes without root privileges.
- **Files:** `src/mios-rs/crates/mios-node/src/sandbox/wasm.rs`, `src/mios-rs/crates/mios-node/Cargo.toml`.
- **Accept:** Guest WebAssembly binaries run deterministically within fuel and memory constraints, returning structured results to the host.
- **Deps:** `NODE-01`.

### NODE-03 — LWW-Element-Set and Vector Clock CRDT Engine for Edge Nodes  **[P1]**
- **What:** Implement Last-Write-Wins Element-Set and Vector Clocks for task assignment, node presence, and shared key-value registers.
- **Why:** Guarantees lock-free eventual consistency across intermittent network partitions.
- **Files:** `src/mios-rs/crates/mios-node/src/crdt.rs`, `src/mios-rs/crates/mios-node/src/state.rs`.
- **Accept:** Network partitions heal automatically with deterministic CRDT state convergence across all participating nodes.
- **Deps:** `NODE-01`.

### NODE-04 — Avahi/mDNS Zero-Conf Mesh Discovery and Handshake  **[P2]**
- **What:** Implement mDNS service registration (`_mios-node._tcp`) and mutual TLS/Ed25519 challenge-response handshake.
- **Why:** Enables zero-configuration onboarding of edge worker nodes into the MiOS mesh.
- **Files:** `src/mios-rs/crates/mios-node/src/discovery.rs`, `usr/share/mios/mios.toml`, `usr/lib/systemd/system/mios-node.service`.
- **Accept:** Newly booted edge nodes discover the host cluster via mDNS and appear in `/v1/cluster/nodes` automatically.
- **Deps:** `NODE-01`.

## WS-DOCGEN — Generative documentation: one pipeline from AI-hints and comments to every doc surface
<!--
id: WS-DOCGEN
title: Generative documentation — AI-hints and comments harvested into the manual, wiki and machine-facing surfaces
theme: Testing & Conformance
status: active
priority: P1
laws: [7, 8, 14]
ssot_keys: ["docs"]
adr: [11]
deps: [WS-DOCS]
acceptance: |
  Every doc surface is either authored prose or a marker interior derived from
  the SSOT; a comment's knowledge is provably landed in a doc before the comment
  can be pruned; and the narrative ratchet can actually fall, because a harvest
  path exists to move prose out of source and into the manual.
-->

*The header engine, lexer/classifier, marker splicer and ratchet already exist and work — the programme is not a rewrite. Mapped end to end in a 2026-08 cross-repo research pass over `mios.git` and `mios-bootstrap.git`; the two missing links are `harvest` (nothing can move a comment into a doc) and the derived surfaces the derivers were specified for. Programme document: `docs/design/doc-generative-documentation.md`; tasks AGY-1580..1594 in `AGY-TASKS.md`.*

### DOCGEN-01 — Gate the corpus ledger and repair the landing predicate  **[P1] ✅ DONE**  (→ T-294)
- **What:** `Policy.landing_min_word_ratio` wired from `[docs]` so `mios-manual landed()` stops raising; `check_manual_ledger` + negative test; ledger regenerated and made the last step of `tools/sync-generated.sh`; both manual surfaces registered in the Law-8 projection registry; `test_mios_comments.py` wired into the gate.
- **Why:** `landed()` is the predicate authorising every future deletion and it was dead code, so the ratchet could only ever rise; the ledger was stale with nothing watching it.
- **Files:** `usr/lib/mios/mios_comments.py`, `usr/lib/mios/test_mios_comments.py`, `automation/98-drift-checks.sh`, `tests/drift-gate-negatives.sh`, `tools/sync-generated.sh`, `usr/share/mios/mios.toml`, `Justfile`.
- **Accept:** `mios-manual ledger --check` green; landing ratio correct at the 0.90 boundary; 157 gate-index rows; ratchet held at its ceiling.
- **Deps:** none.

### DOCGEN-02 — Durable ratchet floor  **[P2] ✅ DONE**  (→ T-295)
- **What:** A recorded low-water mark (`coverage --write-floor`) so a lowered ceiling cannot be silently raised back; `check_docs_ratchet_monotone` currently compares only against HEAD and exits 0 when the previous TOML will not parse.
- **Why:** A ratchet that can be wound back is not a ratchet.
- **Files:** `usr/libexec/mios/mios-manual`, `usr/share/mios/reference/doc-ratchet-floor.tsv`, `automation/98-drift-checks.sh`.
- **Accept:** lowering then raising a ceiling in one commit fails the gate.
- **Deps:** `DOCGEN-01`.

### DOCGEN-03 — `mios-manual harvest` — the missing link  **[P1] ✅ DONE**  (→ T-296)
- **What:** The subcommand that moves a MIGRATE comment block into an authored doc passage, stamps the `mios-src:<sha12>` anchor, and fills the ledger's `landed_*` columns.
- **Why:** Without it nothing can ever populate the landing columns, so `landed()` can only return False and the narrative count can never fall — the ratchet is at its ceiling with no legal way down.
- **Files:** `usr/libexec/mios/mios-manual`, `usr/share/doc/mios/manual/`, `usr/share/mios/reference/manual-corpus.tsv`.
- **Accept:** a harvested block flips `landed()` false→true and the narrative count drops by one.
- **Deps:** `DOCGEN-01`.

### DOCGEN-04 — `check_comment_landing` + `mios-manual prune`  **[P1] ✅ DONE**  (→ T-297)
- **What:** Delete a source comment only once its knowledge is provably landed, gated so a stub can never authorise removing a design note.
- **Why:** Completes the loop harvest opens; the strictness of `landed()` is the whole safety story.
- **Files:** `usr/libexec/mios/mios-manual`, `automation/98-drift-checks.sh`, `tests/drift-gate-negatives.sh`.
- **Accept:** `prune` refuses when the predicate is false; round-trip covered by a test.
- **Deps:** `DOCGEN-03`.

### DOCGEN-05 — Widen render scope and add the remaining deriver families  **[P2] ✅ DONE**  (→ T-298)
- **What:** Only `ports` and `laws` of nine specified deriver families exist. Add the rest (pipeline, verbs, units, index, related, api, root-exceptions, the boilerplate block duplicated 69 times), widen `render` beyond `usr/share/doc/mios/`, make an unknown deriver ARG fail loudly as unknown KINDs already do, and close the negative-test hole so the gate also proves prose OUTSIDE a marker stays green.
- **Why:** Without that second assertion the gate could be satisfied by a generator that owns whole files — the precise failure the marker protocol exists to prevent.
- **Files:** `usr/libexec/mios/mios-manual`, `tests/drift-gate-negatives.sh`, `usr/share/doc/mios/reference/`.
- **Accept:** each family renders from the SSOT and is drift-gated; a bad deriver arg fails the run.
- **Deps:** `DOCGEN-01`.

### DOCGEN-06 — Reproducible manual and machine-facing surfaces  **[P2] ✅ DONE**  (→ T-299)
- **What:** `manual.md` is not reproducible and is ungated — regenerating it destroys its H1 and table of contents, and it carries stale automation numbers and retired ports. Rebuild it as authored chapters plus marker interiors, and give `llms.txt`/`llms-full.txt` and `usr/share/doc/mios/README.md` the same treatment. Retire the `just manual` generator that silently rewrites a shipped artifact.
- **Why:** A shipped manual that contradicts the SSOT is how external research inherits wrong facts.
- **Files:** `usr/share/doc/mios/manual.md`, `usr/share/doc/mios/manual/`, `llms.txt`, `llms-full.txt`, `Justfile`, `tools/generate-manual.py`.
- **Accept:** regenerating changes nothing; no `file:///C:/` paths survive; ports match `[ports]`.
- **Deps:** `DOCGEN-05`.

### DOCGEN-07 — Cross-repo participation for mios-bootstrap  **[P2] ✅ DONE**  (→ T-300)
- **What:** `mios-bootstrap.git` carries AI-hints on 69 of 98 taggable files but ships no docgen tooling. Let the CLIs run against it by root rather than copying them across (Law 15 forbids double-tracking the surface).
- **Why:** The operator-facing half of MiOS is currently undocumentable by the same pipeline.
- **Files:** `usr/libexec/mios/mios-manual`, `usr/libexec/mios/mios-ai-tag`, `tools/log-to-bootstrap.sh`.
- **Accept:** one command documents either repo; no tool is duplicated into the sibling.
- **Deps:** `DOCGEN-05`.

## WS-MINI — MiOS-Metal Blades: the bare-metal plane, and a fleet of 2-6 that actually clusters
<!--
id: WS-MINI
title: "MiOS-Metal Blades: the bare-metal plane, and a fleet of 2-6 that actually clusters"
theme: Fleet & Federation
status: planned
priority: P0
laws: [1, 6, 7, 8, 11, 12, 14, 16]
ssot_keys: ["blades", "blades.hazards", "metal", "metal.gpu", "metal.mesh", "blade.archetypes", "containers.mios-k3s", "storage.cephfs", "services.ceph", "packages.ha"]
adr: [16]
deps: [WS-BLADE, WS-NODE]
acceptance: |
  A 2-6 Blade fleet forms one k3s cluster rather than N control planes, cannot reach unfenced multi-node Pacemaker, and expresses hardware-facing roles only on the Blade plane.
-->

A **MiOS-Metal Blade** (formerly Mini) is *bare metal*. It owns the NICs, radios, TPM, boot
chain and dGPUs, and hosts the MiOS OCI image as a **NIC-less guest obfuscated from all of
it**. A fleet is **2-6 Blades**, and **every Blade is its own AP forming one mesh Wi-Fi**, as
well as a member of the **HCI mesh VPN cluster** that the hyper-converged lanes actually run
over. **No hosted node is ever an access point.**

T-331 established that MiOS-Metal is the box and that most of what it is defined by is a
target. T-333 measured how far: a six-lane audit against "if someone booted this image on a
mini-PC today, would it work?" answered *no* for every lane, and the reasons differ — Wi-Fi AP
and mesh transport and router core are **absent** (`hostapd` appears in three documents and no
unit; `wireguard`/`netbird`/`nebula`/`zerotier`/`VXLAN` are zero hits repo-wide), the
hypervisor is **gated off**, Ceph is a **single-node MON-only scaffold whose bootstrap never
touches a block device**, and HA is a **one-node unfenced Pacemaker cluster**.

The finding that matters most is not any single lane. `[blade.requires]` already generates k3s
`nodeSelector`s and Pacemaker location rules, and **nothing consumes them at runtime** — the
placement machinery for a fleet is projected, gated and inert. That is the same decorative-key
failure as `[greenboot].blade_reachability_critical` and `[security].api_require_auth`, except
it is the whole scheduler axis rather than one flag.

**Quorum is not Blade-bound, which is what makes a two-Blade fleet a supported HA shape.** A
cluster vote needs no hardware, so a Blade **spawns a hosted MiOS container image as an
additional voting member** to complete an odd count until a real Blade joins the mesh, and
services fail over to the surviving clustered Blades. That removes the qdevice, the qnetd
arbiter and the two-node special case from the design entirely — the arbiter is the OS
itself. The caveat that must be stated rather than assumed: a filler spawned *on* one of two
Blades costs two votes when that Blade dies, so its placement (an Edge node, or a named weaker
Blade) is a decision, not a detail.

Placement has a second axis the SSOT also cannot express. **CORE services — storage, personal
files, settings and configuration — may be hosted only by Blades and Edge nodes**, never by a
plain hosted Node, because they own the data that must outlive any one guest. With the two
hardware-facing shapes that gives four role shapes in total, which is why T-988 stops being a
formality about VFIO and becomes the task that declares the boundary the other six consume.

T-334 declared the fleet size (`[blades]` `min_nodes`/`typical_nodes`/`max_nodes`) so a gate
could finally distinguish "correct for one node" from "broken above one", and registered the
two hazards that are *dangerous rather than merely missing* in the shrink-only
`[blades.hazards].accepted`: `k3s-multi-server` (four archetypes grant what `mios-k3s`
requires, and it runs `k3s server` with no `K3S_URL`, so three default Minis are three
independent control planes sharing one token) and `pacemaker-unfenced`
(`stonith-enabled=false`, correct on the one node it creates, and how split-brain corrupts
data once a peer exists). Both are accepted, not fixed. This workstream fixes them.

### MINI-05 — Seven lanes, each with a stated target shape at 2-6 nodes  **[P0]**  (→ T-333, split into T-985..T-991)
- **What:** Give every audited lane its own task and its own target shape, and gate the two
  dangerous ones so neither is reachable by adding a second machine. **T-985** an AP on every
  Blade with one SSID, non-overlapping generated channels, 802.11r/k/v roaming and a declared
  backhaul — and `radio` unclaimable by any hosted-image archetype. **T-986** universal mesh
  membership per Blade under a singleton `headscale` coordinator that degrades open (Law 12),
  keeping the Wi-Fi mesh and the VPN mesh distinct in SSOT. **T-987** serving everywhere, WAN
  uplink in exactly one place, `nftables` inet + flowtable and `dnsmasq` generated from SSOT,
  no firewalld. **T-988** the plane boundary itself: declare it once, plus the four role
  shapes the SSOT cannot currently express — *universal-per-Blade*, *singleton-across-Blades*,
  *Blade-or-Edge-only* (CORE services) and *guest-plane* — so the other six lanes consume it
  rather than re-deriving it. **T-989** an odd MON count, never two, reached by spawning a
  member when the Blade count is even; an OSD on the block device the Blade presents; and the
  `ConditionVirtualization=no` that guarantees Ceph never bootstraps on the one topology it
  targets. **T-990** one `--cluster-init` server with the rest joining, and a spawned third
  server at two Blades since two etcd members cannot hold quorum at `(n/2)+1`, plus the join
  token out of a world-readable generated Quadlet. **T-991** an odd member count reached the
  same way — no qdevice, no two-node mode — with fencing still mandatory above one member
  and a fence agent that acts through the Blade, because a guest cannot power-cycle
  anything.
- **Why:** A configuration that only works standalone is a defect waiting for the operator to
  add a peer, and it is invisible until the SSOT says how many peers there are. Two of these
  are worse than invisible: they fail silently into data corruption or into N clusters that
  each believe they are the cluster.
- **Files:** `usr/share/mios/mios.toml` (`[blades]`, `[metal.*]`, `[blade.archetypes]`,
  `[containers.mios-k3s]`, `[storage.ceph]`, `[ha]`), `usr/lib/systemd/system/mios-ha-bootstrap.service`,
  `usr/libexec/mios/ceph-bootstrap.sh`, `usr/share/containers/systemd/mios-k3s.container` (generated),
  `tools/native/mios-unit-gen/src/lib.rs`, `tools/generate-pod-quadlets.py`, `tools/check-fleet-safety.py`,
  `tools/native/`.
- **Accept:** each lane states its target shape for 2-6 nodes; `k3s-multi-server` and
  `pacemaker-unfenced` retire themselves from `[blades.hazards].accepted` because the detector
  stops reproducing them, never by editing the list; and the guards read live state — the
  running corosync nodelist, the generated Quadlet — rather than a comment or an intent key.
- **Deps:** `WS-BLADE` (the capability/archetype machinery this makes consume its own output).
- **Agent lanes:** the code-only half is `AGY-2583` (declare the boundary + three role shapes),
  `AGY-2584` (make hardware-facing capabilities unclaimable by a guest), `AGY-2585` (k3s role
  from SSOT, `K3S_URL` for joiners, token to `secrets.env`), `AGY-2586` (fail closed on
  unfenced multi-node Pacemaker, reading the live nodelist, as a Rust static binary).

## WS-NODE-ANDROID — Android edge micro-node over CDC-NCM USB tethering
<!--
id: WS-NODE-ANDROID
title: Android edge micro-node over CDC-NCM USB tethering
theme: Fleet & Federation
status: proposed
priority: P2
laws: [1, 7, 8, 9, 14]
ssot_keys: ["containers.mios-node", "blade.discovery"]
adr: [16, 20]
deps: [WS-NODE, WS-BLADE, WS-MINI]
acceptance: |
  An Android handset attached over USB enumerates as a CDC-NCM interface, runs an AArch64 mios-node built by CI, answers mDNS on the tethered link only, executes Wasm tasks on-device, reconciles its task ledger across unplug/replug, and drains its work when the handset gets hot or low on charge.
-->

Nothing Android-facing exists in the tree today: no tether tooling, no AArch64 target, no
`[edge.*]` SSOT table, and no task or workstream had claimed the ground before this one. The
substrate this builds on is real — `src/mios-rs/mios-node/src/protocol.rs` implements the
16-byte header and `MIOS_PORT_NODE` is already allocated at 8650 — so this workstream is the
transport, the target triple, and the honesty repairs, not a new mesh.

**Corrections carried in from the source research (do not re-import the originals):**
- USB product ID `0x4EE3` is **RNDIS**, not "NCM + ADB". Google's NCM IDs are `18d1:4eeb`
  (NCM) and `18d1:4eec` (NCM + ADB), which is what a udev rule must match.
- Mainline has **not** removed or blacklisted `rndis_host`. Removal has been proposed
  repeatedly since 2022 and never merged; `CONFIG_USB_NET_RNDIS_HOST` is still a plain
  tristate. Prefer NCM on its merits (aggregation, macOS/Windows class drivers), not on a
  deprecation that has not happened.
- The stock AOSP RNDIS instance is `rndis.gs4`, not `rndis.gs7`, and Android's own init
  unbinds by writing `none` to `UDC` rather than an empty string.
- In-box auto-binding for `UsbNcm.sys` is a **Windows 11** property; Windows 10 shipped the
  binary but did not bind it to NCM devices without a manual INF.
- NCM-by-default is a **Tensor Pixel (Pixel 6+, Android 12 Gadget HAL v1.2)** property, not
  an AOSP-wide default; the tether function stays a per-vendor choice.
- PRoot's cost is not a flat "20-40%": it is `ptrace` on every syscall, which is tens of
  percent on compute and several-fold on syscall-dense I/O.
- The process assassin on Android 12+ is the **phantom process killer**, which a foreground
  service does not stop; it needs the `max_phantom_processes` device-config escape.
- MiOS ports are allocated, not chosen: the source cited `:11450`/`:11441`/`:8080` and
  Postgres `:5432`. The real values are `llm_light` 8500, `vllm` 8520, `sglang` 8530,
  `pgvector` 8600, `agent_pipe` 8700 (which is `MIOS_AI_ENDPOINT`), `node` 8650. Three of the
  cited ports are in `[docs].retired_ports`.

### NODEDROID-01 — CDC-NCM gadget orchestration and host link bring-up  **[P2]**  (→ T-976)
- **What:** Add `mios-tether-android` to negotiate the USB function (report, and optionally switch, `rndis.gs4` → an NCM instance) and a udev rule that brings the host side up when `18d1:4eeb`/`18d1:4eec` appears. Introduce `[edge.android_tether]` in the SSOT **with its consumer in the same change** — addresses, interface name and protocol preference are read from it, never written as literals.
- **Why:** The link is the precondition for every other item, and it is the one place where a wrong constant (the `0x4EE3` in the source document) silently produces an RNDIS link that no macOS host can use.
- **Files:** `usr/libexec/mios/mios-tether-android`, `usr/lib/udev/rules.d/99-mios-ncm.rules`, `usr/share/mios/mios.toml`.
- **Accept:** Attaching a Pixel-class handset yields an `usb0` carrying the SSOT-declared address; a handset that enumerates as RNDIS is **reported as degraded**, not silently accepted; no IP, interface name or product ID appears as a literal in the script.
- **Deps:** none.

### NODEDROID-02 — AArch64 target for mios-node  **[P2]**  (→ T-977)
- **What:** Give the existing crate an Android/AArch64 build path and a packaging script. Decide the linkage explicitly: `aarch64-unknown-linux-musl` for a genuinely static Termux binary, or `aarch64-linux-android` accepting bionic dynamic linking — `-C target-feature=+crt-static` is not dependable on the Android targets.
- **Why:** `mios-node` is x86_64-only today; without a target triple the edge node is a document, not a deployment.
- **Files:** `src/mios-rs/mios-node/Cargo.toml`, `src/mios-rs/.cargo/config.toml`, `automation/55-native-build.sh`, `tools/package-android-node.sh`.
- **Accept:** CI emits an AArch64 artifact and `mios-node --version` runs on-device under Termux. Also repairs WS-NODE's `Files:` citations, which point at `src/mios-rs/crates/mios-node/` — a path that does not exist.
- **Deps:** `NODEDROID-01`.

### NODEDROID-03 — Real mDNS on the tethered link  **[P2]**  (→ T-978)
- **What:** Replace the placeholder in `usr/libexec/mios/node/discovery.py`, which advertises "Avahi/mDNS zero-conf with an Ed25519 handshake" while opening no socket and using a symmetric HMAC, with a responder that actually answers `_mios-node._tcp.local`, bound to the tether interface. Add `tether` to `[blade.discovery].order`.
- **Why:** Discovery is the difference between a cable and a cluster, and the current module's docstring is a claim its code does not support.
- **Files:** `usr/libexec/mios/node/discovery.py`, `src/mios-rs/mios-node/src/net.rs`, `usr/share/mios/mios.toml`.
- **Accept:** A node is resolvable over the tether within seconds of attach, and a negative test proves the responder answers on **no** other interface.
- **Deps:** `NODEDROID-01`, `NODEDROID-02`.

### NODEDROID-04 — Wasm execution in the native node  **[P2]**  (→ T-979)
- **What:** Put a Wasm engine in the Rust crate (which has none — the sandbox exists only as `usr/libexec/mios/node/wasm_sandbox.py`) with fuel metering, and fall back to the Pulley interpreter where a JIT mapping is refused.
- **Why:** WS-NODE's NODE-02 already claims Wasmtime integration; this closes the gap between that claim and `Cargo.toml`.
- **Files:** `src/mios-rs/mios-node/Cargo.toml`, `src/mios-rs/mios-node/src/executor.rs`.
- **Accept:** A fuel-metered module runs on-device; exhausting fuel terminates it; the interpreter path is exercised by a test, not assumed.
- **Deps:** `NODEDROID-02`.

### NODEDROID-05 — Ledger reconciliation across a cable that moves  **[P2]**  (→ T-980)
- **What:** Make task-ledger sync survive unplug/replug: persist local mutations while detached and reconcile on reattach. Correct the naming while here — `usr/libexec/mios/node/crdt.py` is a last-write-wins register map with tombstones, not the LWW-**Element-Set** it is labelled, and `state_sync.rs` merges a vector clock it never reads.
- **Why:** A USB cable is a transient link by nature; and a CRDT named after an algorithm it does not implement misleads every later reader.
- **Files:** `usr/libexec/mios/node/crdt.py`, `src/mios-rs/mios-node/src/state_sync.rs`.
- **Accept:** Detach mid-task, mutate on both sides, reattach: no write is lost and no merge blocks. The type's name matches its implementation, or the implementation matches its name.
- **Deps:** `NODEDROID-03`.

### NODEDROID-06 — Battery and thermal watchdog  **[P2]**  (→ T-981)
- **What:** Add `mios-android-health` plus a greenboot **wanted.d** check that drains work from a handset that is too hot or too low on charge. Thresholds come from `[edge.android_tether]`.
- **Why:** The edge node runs on a battery inside a phone; sustained inference is a thermal event, and a node that dies mid-task without draining strands the DAG.
- **Files:** `usr/libexec/mios/mios-android-health`, `usr/lib/greenboot/check/wanted.d/70-mios-android-edge.sh`.
- **Accept:** Crossing either threshold drains and refuses new work; the check is `wanted.d`, so a degraded handset never blocks host boot (Law 12).
- **Deps:** `NODEDROID-01`, `NODEDROID-03`.

### NODEDROID-07 — Wireless join over the Blade mesh AP; the cable becomes the enrolment channel  **[P2]**  (→ T-992)
- **What:** NODEDROID-01..06 are entirely cable-bound, which makes the edge node a peripheral rather than a node. WS-MINI establishes that **every MiOS-Metal Blade is its own AP forming one mesh Wi-Fi**, so the handset already has a network to join. Split the two paths by what each is actually good at: **USB is the out-of-band enrolment channel** — physically authenticated by the act of plugging in, and the only channel that exists before the device has credentials — and **Wi-Fi over the Blade mesh is the operational path**, so the node keeps working in a pocket. `[edge.android_tether]` gains a peer `[edge.android_wifi]`, and the node's link state becomes a first-class thing it reports rather than an assumption baked into the transport.
- **Why:** A phone tethered to a Blade by a cable is a slower USB accelerator. A phone that enrols over the cable once and then works over the mesh is an edge node, which is the thing the workstream is named for. It also removes the single most awkward property of the current design: that the handset must stay physically attached to a machine that is itself supposed to be a headless box in a rack.
- **Files:** `usr/libexec/mios/mios-tether-android`, `usr/libexec/mios/node/discovery.py`, `src/mios-rs/mios-node/src/net.rs`, `usr/share/mios/mios.toml` (`[edge.android_wifi]`).
- **Accept:** A handset enrolled over USB rejoins over Wi-Fi after unplug with no re-enrolment; the mDNS responder from NODEDROID-03 answers on the tether **or** the mesh link and on no other interface; and a node that has never been enrolled over the cable cannot join over Wi-Fi at all.
- **Deps:** `NODEDROID-03`, `NODEDROID-10`, WS-MINI T-985.

### NODEDROID-08 — Survive Android's process lifecycle, or admit the node is unreliable  **[P1]**  (→ T-993)
- **What:** Android 12+ runs a **phantom process killer**: forked child processes are counted against a limit that defaults to **32 across the whole system** — not per app — and the excess is SIGKILLed. This is why Termux-hosted workloads die with `[Process completed (signal 9)]` at seemingly random intervals, and a foreground service does not prevent it. The documented escape is `device_config put activity_manager max_phantom_processes <large>` plus `settings put global settings_enable_monitor_phantom_procs false`, which requires **adb or root** — a one-time provisioning step, not something the node can do for itself. Three things follow and all three belong in the tree rather than in a wiki: perform that provisioning **while the cable is attached** (NODEDROID-07's enrolment moment is exactly when adb is available); **keep the node's own process count near one**, because every child it forks is drawn from a budget shared with every other app on the phone; and **detect that it was killed** and re-establish rather than silently vanishing from the fleet.
- **Why:** This is the difference between an edge node and an edge node that works. The correction is already recorded at the top of this workstream — the phantom killer, not a foreground-service limitation — but no item owned it, so the design still assumes a process that stays alive. A node that can be SIGKILLed at any moment without the fleet noticing strands whatever DAG depended on it.
- **Files:** `usr/libexec/mios/mios-tether-android`, `usr/libexec/mios/mios-android-health`, `src/mios-rs/mios-node/src/main.rs`, `usr/share/mios/mios.toml` (`[edge.android_tether]`).
- **Accept:** Enrolment reports whether the phantom-process escape was applied and the node advertises itself as **degraded** when it was not, rather than presenting as healthy and dying later; the node's steady-state child-process count is asserted by a test; and a SIGKILLed node is observed missing by the fleet within a bounded interval.
- **Deps:** `NODEDROID-02`.

### NODEDROID-09 — On-device inference that does not mortgage Law 5  **[P2]**  (→ T-994)
- **What:** Decide the Android inference path deliberately, because the obvious one is a trap. **NNAPI was deprecated in Android 15**, so it is not a foundation to build on. The vendor-blessed successors split: **LiteRT** (shipped in Play Services) and **AICore/Gemini Nano** are the Google path, and AICore in particular is a **vendor-coupled hosted-model dependency** that Law 5 (UNIFIED-AI-REDIRECTS) does not permit — every agent and tool targets `MIOS_AI_ENDPOINT`, and no vendor-specific model product belongs in the tree. The path that costs MiOS nothing architecturally is the one it already ships: **`llama.cpp` with the same GGUF and the same OpenAI `/v1` surface as `mios-llm-light`**, so an Android edge node is a *lane* like any other and the router does not learn a new protocol to reach it. **ExecuTorch** (QNN / MediaTek backends, XNNPACK CPU fallback) is the considered alternative *only* where a specific NPU is worth the coupling, and that trade is recorded rather than assumed.
- **Why:** The handset's accelerator is the reason to put a node on it at all, and the cheapest way to lose the whole sovereignty thesis is to reach it through a hosted vendor runtime. Choosing llama.cpp keeps the edge node inside the existing endpoint contract; choosing AICore would put a cloud-adjacent vendor model inside an OS whose premise is that it has none.
- **Files:** `src/mios-rs/mios-node/Cargo.toml`, `usr/share/mios/mios.toml` (`[nodes.*]`, `[edge.android_tether]`), `usr/share/doc/mios/adr/`.
- **Accept:** The handset answers `/v1/models` and serves a chat completion from a GGUF the fleet already bakes; NNAPI appears nowhere; an ADR records why AICore/Gemini Nano was rejected on Law 5 grounds and under what conditions ExecuTorch would be adopted instead.
- **Deps:** `NODEDROID-02`, `NODEDROID-04`.

### NODEDROID-10 — A device identity the handshake actually implements  **[P1]**  (→ T-995)
- **What:** `usr/libexec/mios/node/discovery.py` advertises "Avahi/mDNS zero-conf with an **Ed25519 handshake**" while opening no socket and using a **symmetric HMAC**. NODEDROID-03 replaces the socket half; this item owns the identity half, because a shared symmetric secret is not an identity — every node that can verify a peer can also impersonate one, which is precisely the property a fleet admitting pocket-sized hardware cannot afford. Give each edge node a real asymmetric device identity, enrolled over the USB cable (the one channel authenticated by physical possession), and bind it to the existing agent-passport / A2A identity surface rather than inventing a second trust root.
- **Why:** An Android handset is the least physically-controlled member of the fleet — it leaves the building, it gets lost, it runs other people's apps. It is the member whose credential most needs to be revocable and least deserves to be the same secret everyone else holds. And the current module's docstring is a claim its code does not support, which is the same defect class as the decorative keys T-333 found: it reads as solved.
- **Files:** `usr/libexec/mios/node/discovery.py`, `src/mios-rs/mios-node/src/net.rs`, `usr/share/mios/mios.toml`, `/etc/mios/secrets.env`.
- **Accept:** Enrolment provisions a per-device keypair whose private half never leaves the handset; a peer holding only public material can verify but not impersonate; revoking one device does not require re-keying the fleet; and the docstring matches the implementation, in whichever direction is made true.
- **Deps:** `NODEDROID-03`.

# Testing & Conformance

## WS-TESTDOC — Testing, drift-gate negative coverage, and documentation integrity
<!--
id: WS-TESTDOC
title: Testing, drift-gate negative coverage, and documentation integrity
theme: Testing & Conformance
status: done
priority: P1
laws: [7, 8]
ssot_keys: ["testing.smoke_components", "build.bake"]
adr: [12]
deps: []
acceptance: |
  All drift-checks are backed by negative tests, shellcheck error coverage spans all shell directories, and bake-smoke testing is driven from SSOT.
-->

### TESTDOC-01 — Drift-gate negative self-tests in CI and shellcheck coverage ratcheting  **[P1]**
- **What:** CI runs `drift-gate-negatives.sh`, `lint-shell.sh` lints all shell directories, and `tests/bake-smoke.sh` extracts smoke testing into a reusable harness.
- **Why:** Protects against gate regressions and ensures all shell code meets quality standards.
- **Files:** `automation/lint-shell.sh`, `tests/drift-gate-negatives.sh`, `tests/bake-smoke.sh`, `.github/workflows/mios-ci.yml`.
- **Accept:** `just drift-gate` is green, negative tests run in CI, and `lint-shell.sh` covers all shell scripts.
- **Deps:** none.

# Deployment & Sovereignty

## WS-DEPLOY — Deployment surface consolidation and bare-metal bootc install
<!--
id: WS-DEPLOY
title: Deployment surface consolidation and bare-metal bootc install
theme: Deployment & Sovereignty
status: done
priority: P1
laws: [1, 3, 4, 12]
ssot_keys: ["build.bake"]
adr: [13, 14]
deps: []
acceptance: |
  Single deployment dispatcher behind installation/mios-install, supporting offline bootc install to-disk.
-->

### DEPLOY-01 — Single install front door and offline bootc bare-metal leg  **[P1]**
- **What:** Consolidates deployment entrypoints under `installation/mios-install.{sh,ps1,bat}` and defines the three `bootc` install legs (`to-existing-root`, `to-disk`, `to-filesystem`).
- **Why:** Provides a single unified installation API and enables offline bare-metal installation from USB/OCI payload tarballs.
- **Files:** `installation/mios-install.sh`, `installation/mios-install.ps1`, `usr/share/doc/mios/adr/0013-deploy-surface-consolidation.md`, `usr/share/doc/mios/adr/0014-bootc-install-bare-metal-leg.md`.
- **Accept:** `mios-install` resolves targets with `--dry-run`, ADR-0013 and ADR-0014 accepted/proposed.
- **Deps:** none.

# Global Unification & De-duplication

## WS-DEDUP-DISCOVER — Value duplication reporting, cross-surface scanner, and enforcement gates
<!--
id: WS-DEDUP-DISCOVER
title: Value duplication reporting, cross-surface scanner, and enforcement gates
theme: Global Unification & De-duplication
status: done
priority: P1
laws: [7, 8]
ssot_keys: ["build.bake"]
adr: [12]
deps: []
acceptance: |
  Value duplication report tool emits classified TSV, cross-surface scanner flags hardcoded literals in non-SSOT files, and drift gates enforce zero unallowlisted value duplication.
-->

### DEDUP-DISCOVER-01 — Value duplication report tool, literal scanner, and drift gates  **[P1]**
- **What:** Build `mios-dup-report` and `mios-cross-surface-scan`, extend `check_no_duplicate_value_key`, add `check_no_hardcoded_ssot_literal`, and audit alias emissions.
- **Why:** Establishes measurement-first enforcement for the full de-duplication campaign.
- **Files:** `tools/mios-dup-report`, `tools/mios-cross-surface-scan`, `automation/98-drift-checks.sh`, `tests/drift-gate-negatives.sh`.
- **Accept:** `mios-dup-report` emits TSV report, scanner flags literals, drift-gate check green.
- **Deps:** none.

## WS-DEDUP-COLOR — Color and theme alias collapse to single SSOT representation
<!--
id: WS-DEDUP-COLOR
title: Color and theme alias collapse to single SSOT representation
theme: Global Unification & De-duplication
status: done
priority: P2
laws: [8, 9]
ssot_keys: ["colors"]
adr: [10]
deps: [WS-DEDUP-DISCOVER]
acceptance: |
  Every color role resolves to a single canonical name across themes, desktop elements, and shell surfaces with zero visual or functional drift.
-->

### DEDUP-COLOR-01 — Theme color canonicalization and redundant name collapse  **[P2]**
- **What:** Reconcile `COLOR_*` vs `COLORS_*` and ANSI singular/plural naming, repoint consumers, and update resolver twins.
- **Why:** Eliminates 7x per-color key multiplication while maintaining full lossless rendering.
- **Files:** `usr/lib/mios/mios_toml.py`, `tools/lib/userenv.sh`, `usr/libexec/mios/mios-theme-render`.
- **Accept:** Theme projection gate green, zero visual change, single canonical color keys.
- **Deps:** `WS-DEDUP-DISCOVER`.

## WS-DEDUP-AIPLANE — AI model, embed, endpoint, and engine key consolidation
<!--
id: WS-DEDUP-AIPLANE
title: AI model, embed, endpoint, and engine key consolidation
theme: Global Unification & De-duplication
status: done
priority: P2
laws: [7, 8, 13]
ssot_keys: ["ai"]
adr: [6]
deps: [WS-DEDUP-DISCOVER]
acceptance: |
  AI plane endpoints, models, and embeddings derive from unified canonical declarations.
-->

### DEDUP-AIPLANE-01 — AI key family collapse and consumer repointing  **[P2]**
- **What:** Unify embedding model keys into `MIOS_AI_EMBED_MODEL`, collapse endpoints into `MIOS_AI_ENDPOINT`, and reconcile `MIOS_AI_VLLM_*` vs `MIOS_VLLM_*`.
- **Why:** Reduces 6-10x fanout per AI concept down to minimal canonical keys.
- **Files:** `usr/lib/mios/mios_toml.py`, `tools/lib/userenv.sh`, `usr/lib/mios/agent-pipe/server.py`.
- **Accept:** AI plane consumers function unchanged, env diff lossless, twin equivalence green.
- **Deps:** `WS-DEDUP-DISCOVER`.

## WS-DEDUP-NETPATH — Bind address, browser flags, sub-dirs, and port offset derivation
<!--
id: WS-DEDUP-NETPATH
title: Bind address, browser flags, sub-dirs, and port offset derivation
theme: Global Unification & De-duplication
status: done
priority: P2
laws: [7, 8]
ssot_keys: ["ports", "network"]
adr: [12]
deps: [WS-DEDUP-DISCOVER]
acceptance: |
  Host bind addresses, sub-directory paths, and port offsets derive cleanly from base declarations without redundant literals.
-->

### DEDUP-NETPATH-01 — Net and path key derivation collapse  **[P2]**
- **What:** Derive loopback hosts, browser flag pairs, and sub-directories from parent SSOT declarations.
- **Why:** Eliminates duplicate network and path literals across the codebase.
- **Files:** `usr/lib/mios/mios_toml.py`, `tools/lib/userenv.sh`.
- **Accept:** Zero literal duplication in network/path families, lossless snapshot diff.
- **Deps:** `WS-DEDUP-DISCOVER`.

## WS-DEDUP-STRUCTURAL — Resolver double-emission removal and centralized alias table
<!--
id: WS-DEDUP-STRUCTURAL
title: Resolver double-emission removal and centralized alias table
theme: Global Unification & De-duplication
status: done
priority: P1
laws: [8, 13]
ssot_keys: ["build.bake"]
adr: [12]
deps: [WS-DEDUP-DISCOVER]
acceptance: |
  Single COMPAT-ALIAS table drives all resolver alias emissions with zero unconsumed dupe keys.
-->

### DEDUP-STRUCTURAL-01 — Centralized compat-alias table and twin emitter cleanup  **[P1]**
- **What:** Refactor `get_aliases` rules into a single `COMPAT-ALIAS` declaration table for Python and bash resolver twins.
- **Why:** Prevents resolver logic from minting duplicate unconsumed keys.
- **Files:** `usr/lib/mios/mios_toml.py`, `tools/lib/userenv.sh`, `tools/check-resolver-twin.py`.
- **Accept:** Both twins generate identical minimal aliases, deprecation report functional.
- **Deps:** `WS-DEDUP-DISCOVER`.

## WS-DEDUP-CROSSSURFACE — SSOT projection across docs, HTML, graph, and build manifests
<!--
id: WS-DEDUP-CROSSSURFACE
title: SSOT projection across docs, HTML, graph, and build manifests
theme: Global Unification & De-duplication
status: done
priority: P1
laws: [7, 8]
ssot_keys: ["image.sidecars"]
adr: [12]
deps: [WS-DEDUP-STRUCTURAL]
acceptance: |
  All external surfaces (mios.html, knowledge-graph.json, SBOM, Quadlets, docs) project directly from SSOT via mios-ssot-regen.
-->

### DEDUP-CROSSSURFACE-01 — Universal SSOT projection generator and drift verification  **[P1]**
- **What:** Extend `mios-ssot-regen` to update all derived artifacts from `mios.toml` and enforce literal gate.
- **Why:** Guarantees changing one value in SSOT updates every documentation and manifest surface.
- **Files:** `usr/libexec/mios/mios-ssot-regen`, `automation/98-drift-checks.sh`.
- **Accept:** Single value update automatically regenerates all surfaces; `check_no_hardcoded_ssot_literal` green.
- **Deps:** `WS-DEDUP-STRUCTURAL`.

## WS-DEDUP-GUP56 — Always-latest float, SBOM pinning, and minimal key library
<!--
id: WS-DEDUP-GUP56
title: Always-latest float, SBOM pinning, and minimal key library
theme: Global Unification & De-duplication
status: done
priority: P2
laws: [4, 7, 8]
ssot_keys: ["image.sidecars"]
adr: [3]
deps: [WS-DEDUP-CROSSSURFACE]
acceptance: |
  Sidecar containers float to latest tags with SBOM digest pinning, and global env key count drops to minimal set.
-->

### DEDUP-GUP56-01 — Floating tags, SBOM provenance, and key namespace minimization  **[P2]**
- **What:** Convert sidecar container refs to `:latest` float with build-time SBOM digest recording and derive minimal namespace.
- **Why:** Fully lands GUP Phase 5 & 6.
- **Files:** `usr/share/mios/mios.toml`, `usr/share/mios/artifacts/sbom/bound-images.tsv`, `tools/generate-names-registry.py`.
- **Accept:** Zero hardcoded image tag pins in SSOT, minimal key registry green, SBOM complete.
- **Deps:** `WS-DEDUP-CROSSSURFACE`.

## WS-DEDUP-SIGNOFF — Permanent enforcement, negative coverage, ADR, and campaign signoff
<!--
id: WS-DEDUP-SIGNOFF
title: Permanent enforcement, negative coverage, ADR, and campaign signoff
theme: Global Unification & De-duplication
status: done
priority: P2
laws: [7, 8, 12]
ssot_keys: ["build.bake"]
adr: [12]
deps: [WS-DEDUP-GUP56]
acceptance: |
  Full drift-gate suite green, negative tests cover all dedup gates, ADR committed, memory north-stars updated.
-->

### DEDUP-SIGNOFF-01 — Campaign signoff, ADR, and pre-commit hook integration  **[P2]**
- **What:** Finalize gate suite, add negative test cases, publish ADR, update memory markers, and close campaign.
- **Why:** Guarantees de-duplication improvements are permanently enforced and cannot regress.
- **Files:** `automation/98-drift-checks.sh`, `tests/drift-gate-negatives.sh`, `usr/share/doc/mios/adr/`.
- **Accept:** Full drift-gate suite 100% green, negative coverage ratcheted, ADR merged.
- **Deps:** `WS-DEDUP-GUP56`.

## Campaign: Scripts -> Hardened Compiled Code + Global Unification (2026-08-01)

600 researched tasks (AGY-961..AGY-1560) executing the operator directive to minimize MiOS to
hardened, compiled, tested code rather than loose scripts, and globally unify the codebase.
Method: strangler-fig (golden-parity before delete), compiled SSOT resolver, codegen, cargo-
deny/cosign/SLSA supply-chain, policy-as-code governance. Full task text in the AGY- records of `tasks.jsonl`.

### WS-LANG-AUTO -- Rust-port the 121 automation/*.sh build phases -> a compiled `mios-build` orchestrator crate  **[P1/P2]**  (-> AGY-961..AGY-1011, 51 tasks)
### WS-LANGX -- Port the 144 usr/libexec/mios bash tools -> compiled binaries (Rust)  **[P1/P2]**  (-> AGY-1012..AGY-1065, 54 tasks)
### WS-DEBT-PY -- Harden the 122 usr/libexec python tools + tools/*.py (typed, packaged, tested)  **[P1/P2]**  (-> AGY-1066..AGY-1105, 40 tasks)
### WS-DEBT-PIPE -- Harden agent-pipe: split server.py (9k) + mios_dispatch, typing, pydantic wire schemas, kill eval-verbs, pytest  **[P1/P2]**  (-> AGY-1106..AGY-1150, 45 tasks)
### WS-RESOLVER -- Collapse the 3 parallel SSOT resolvers -> ONE compiled crate + generated bindings  **[P1/P2]**  (-> AGY-1151..AGY-1195, 45 tasks)
### WS-DRIFTRUST -- Port 98-drift-checks.sh (123 checks) -> native miosd Rust checks (WALL-RUST continuation)  **[P1/P2]**  (-> AGY-1196..AGY-1247, 52 tasks)
### WS-TEMPLATE -- Compiled file-pattern/template system + `mios new` scaffolder + candidate Law-14  **[P1/P2]**  (-> AGY-1248..AGY-1282, 35 tasks)
### WS-PWSH -- PS AST parse-gate + decompose/compile the 36 PowerShell scripts  **[P1/P2]**  (-> AGY-1283..AGY-1318, 36 tasks)
### WS-SYSTEMD -- Generate the 168 systemd units from SSOT + unit<->SSOT drift-check + sandboxing ratchet  **[P1/P2]**  (-> AGY-1319..AGY-1365, 47 tasks)
### WS-DOTFILES -- ONE compiled SSOT-projection engine for every dotfiles/theme/config surface  **[P1/P2]**  (-> AGY-1366..AGY-1402, 37 tasks)
### WS-GUP -- Global Unification Plan: collapse the ~2523-key MIOS_* namespace, one-value-derived, lossless-gated  **[P1/P2]**  (-> AGY-1403..AGY-1440, 38 tasks)
### WS-ZEROHC -- Complete ZERO-HARDCODES: float remaining literals + fix the unanchored lint + new [network] keys  **[P1/P2]**  (-> AGY-1441..AGY-1476, 36 tasks)
### WS-SBOM -- Supply-chain + boot hardening as compiled/gated policy (SLSA/cosign/signed-UKI/SBOM/cargo-deny)  **[P1/P2]**  (-> AGY-1477..AGY-1515, 39 tasks)
### WS-TESTGOV -- Testing + CI governance (clippy/deny/proptest/golden/mypy) + a per-domain language-enforcement Law  **[P1/P2]**  (-> AGY-1516..AGY-1560, 45 tasks)

## WS-HCI · Sovereign Hyper-Converged Infrastructure & Self-Replication Fabric
<!-- AI-hint: Sovereign Hyper-Converged Infrastructure (HCI) and autonomous self-replication fabric. -->
<!-- AI-related: usr/libexec/mios/deploy/self_replicate.py, usr/libexec/mios/virt/microvm_migrate.py, usr/lib/mios/ai/tensor_pipeline.py -->

Converges compute, storage, networking, and virtualization into an immutable, self-building, and self-replicating Fedora bootc AI operating system.

### HCI-01 · Autonomous Self-Introspection & Local podman-MiOS-DEV Rebuild Trigger Pipeline **[P1]**
- **Goal:** Autonomous system self-introspection, containerized BIB image compilation in `podman-MiOS-DEV`, and atomic `bootc switch` staging.
- **Tasks:** `T-966`, `T-967`.
- **Files:** `usr/libexec/mios/deploy/self_replicate.py`, `tests/test-self-replicate-build.py`.
- **Status:** done.

### HCI-02 · Zero-Downtime MicroVM State Serialization & Live Migration Handover **[P1]**
- **Goal:** Sub-50ms microVM CPU register and virtio-pmem DAX state serialization across sandbox execution nodes with zero guest data loss.
- **Tasks:** `T-968`, `T-969`.
- **Files:** `usr/libexec/mios/virt/microvm_migrate.py`, `tests/test-microvm-live-migration.py`.
- **Status:** done.

### HCI-03 · Dual-Mode Dynamic Topology Switcher (Seat UI vs Headless Blade) **[P1]**
- **Goal:** Clean zero-leak runtime transitions between interactive workstation Seat mode and headless compute Blade mode.
- **Tasks:** `T-970`, `T-971`.
- **Files:** `usr/libexec/mios/node/topology_switch.py`, `tests/test-seat-blade-transition.py`.
- **Status:** done.

### HCI-04 · PSS-Governed Workstation Swarm Resource Limiter & OOM-Shield **[P1]**
- **Goal:** High-density multi-agent swarm execution supporting >1,500 concurrent workers strictly bounded below 16GB host RAM.
- **Tasks:** `T-972`, `T-973`.
- **Files:** `usr/lib/mios/agent-pipe/pss_regulator.py`, `tests/test-swarm-pss-memory.py`.
- **Status:** done.

### HCI-05 · Distributed Layer-Split Tensor Engine over Authenticated Local RPC Mesh **[P1]**
- **Goal:** Distributed pipeline parallelism splitting 80-layer 70B models across local GPUs and remote RPC worker blades with exact payload math.
- **Tasks:** `T-974`, `T-975`.
- **Files:** `usr/lib/mios/ai/tensor_pipeline.py`, `tests/test-tensor-pipeline-rpc.py`.
- **Status:** done.
