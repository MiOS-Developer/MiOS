<!-- AI-hint: Collapse the ~18-image sidecar fleet onto TWO shared-base images (mios-sys CUDA-free + mios-cuda) to cut the bound-image store ~60GB→~25GB; read before migrating any sidecar's Image=/Exec= or building the shared bases. -->
<!-- AI-related: usr/share/mios/mios.toml ([image.sys] [image.cuda] [image.sidecars] [build].bake_groups), automation/57-mios-sys-build.sh, usr/share/mios/sys/Containerfile, usr/share/mios/cuda/Containerfile, tools/native/mios-gen/src/pod_quadlets.rs, automation/34-render-quadlets.sh, MIOS_SYS_IMAGE, MIOS_CUDA_IMAGE -->
---
adr: 0002
title: MiOS-Sys shared-base sidecar consolidation
status: accepted
date: 2026-07-12
deciders: [operator, ai-pair]
tags: [build, images, consolidation, cuda, sidecars, dedup]
laws: [3, 6, 7, 8, 12]
ssot_keys: [image.sys, image.cuda, image.sidecars, build.bake_groups, build.bake_group]
related_ws: [WS-MIOSSYS]
supersedes: []
superseded_by: []
---

# ADR-0002: MiOS-Sys shared-base sidecar consolidation

## Status
Accepted — 2026-07-12. Design accepted; implementation is PLANNED and waved (see
Consequences). Complementary to ADR-0001's Phase-0 sharded bake, which stays as
the safety margin.

## Context

MiOS is one immutable bootc/OCI Fedora image that is also a local
OpenAI-compatible agentic AI OS; it bakes its whole service fleet into the image
so every deployment ships offline-complete (Law 3 BOUND-IMAGES, Law 12
BAKE-NOT-FETCH). Today that fleet is **~18 upstream sidecar images** pulled into
an additional image store at `/usr/lib/containers/storage`, summing to a
**~60 GB** bound-image store (measured; ~66 GB uncompressed, podman claws a few
GB back). These images share **zero** base-OS blobs with each other: each Go/
Python/Node/native service drags its own full alpine/Ubuntu/distroless base, and
the two GPU engines each carry a near-identical CUDA+cuDNN+PyTorch stack.

**The specific failure:** committing that ~60 GB store overran disk-constrained
CI runners. `mios.toml:8460` documents it verbatim — `podman` exhausting runner
disk while serializing a monolithic ~40–60 GB layer commit. A `buildah commit`
needs the store on disk **plus** ~2–3× transient serialization space, so a 60 GB
store demands ~120–180 GB and blows any ~66 GB standard runner (`exit 125` /
"io: read/write on closed pipe"). ADR-0001's Phase-0 sharding (one `RUN` per bake
group) moves *layer boundaries* so no single commit serializes the whole store —
but the whole store is still ~60 GB, and the largest indivisible whale (~25 GB)
sets a floor that is uncomfortably close to a standard runner's ceiling at 3×.

The operator framed the fix as "let sidecars reference the host's own `/usr` via
`additionalimagestores`." **One load-bearing correction carries through the whole
design:** the host `/usr` is ostree/composefs and the additional store is
containers-storage overlay — **these two backends do not blob-share.** The real,
equally-large lever is *single-base-for-all-services within the store*: `1 × base`
counted once instead of `18 × base`. Same ~60 → ~25 GB outcome the idea targets;
only the stated mechanism needed the fix.

## Decision

Collapse the ~18-image sidecar fleet onto **two shared-base images of one
lineage**, both `FROM ${BASE_IMAGE}` (the exact `ucore-hci:stable-nvidia` the OS
itself derives from at `Containerfile:4`/`:27`):

> - **`localhost/mios-sys`** — the CUDA-free glibc image for the small
>   Go/Python/Node/native services (~6–8 GB), with shared runtime layers:
>   `python` (CPython + fastapi/uvicorn/httpx/pydantic wheels), `node`
>   (open-webui, firecrawl, code-server), `chromium` (crawl4ai), optional `jre`
>   (guacamole web only). Each app is a thin leaf on top.
> - **`localhost/mios-cuda`** — the GPU base carrying the shared CUDA/torch/
>   flashinfer **whale layer** + the engine venvs (~15–18 GB): one L2
>   CUDA/cuDNN/NCCL/torch/flashinfer layer, then **two sibling venvs**
>   (`vllm-venv`, `sglang-venv`) plus `llama-server` linking the same CUDA libs.

Both derive from `${BASE_IMAGE}@<same-digest>`, so podman stores those base
layers **once** (bit-identical). Two venvs (not one shared venv) because vLLM and
SGLang routinely pin **different** torch builds (e.g. `2.8.0+cu128` vs
`2.9.1+cu130`); co-installing conflicts, separate venvs on the shared L2 base
sidestep it while still sharing the ~9–12 GB CUDA/torch bytes.

**Runtime = Model A (unanimous): one IMAGE, many CONTAINERS.** Distinct Quadlets,
shared `Image=`, per-service `Exec=`. Each service keeps its own container, its
own `User=`/`Group=`/`Delegate=yes`, its own restart policy, and its own
per-blade `Condition*` activation gate — **only the image collapses.** This is the
apko/distroless "multiple entrypoints off one base" and Bitnami-minideb "one base,
many runtimes" pattern; MiOS already ships Model A in-tree
(`localhost/mios-crawl4ai-slim`, `localhost/mios-firecrawl`,
`localhost/mios-coderun-sandbox`). Model B (one s6/supervisord fat container) is
**rejected** — it buys port collisions in one netns, one OOM killing siblings, and
a single runtime blast radius.

**Locked parameters (operator):**
- Newest packages globally, tagged/pinned **at build**: MiOS owns the *release*
  (a `MIOS_<X>_VERSION` key per app), verifies upstream `checksums.txt`/`.asc`,
  and records the resolved digest as SBOM (ADR-0003) — never a hand-pinned literal.
- All-core-consolidate: every consolidatable service folds onto the shared bases.
- k3s binary **and** Pacemaker/corosync-HA stay core; their *privileged
  activation* is unchanged by baking the binary.
- Rebuild both bases **weekly + on-CVE / on-release** (no `AutoUpdate=registry`
  for baked apps).
- **Ceph = KEEP-SEPARATE** — the one pulled image (see below).

## Rationale

- **Attacks `exit 125` at the root, not as a palliative.** MiOS-Sys makes the
  store *small* (60 → ~21 GB optimistic / ~27–30 GB conservative), roughly half
  the win from de-duplicating the vLLM/SGLang CUDA+torch whale into one physical
  L2 layer (retiring the post-hoc `use_hard_links` trick in `mios-bake-group`).
  On the math: MiOS-Sys **alone** makes a single-commit bake borderline-feasible
  (25 GB × 2× = 50 GB fits a 66 GB runner); **MiOS-Sys + Phase-0 sharding** caps
  the largest single commit at the ~12 GB CUDA/torch group (~36 GB at 3×) — a
  comfortable fit. So it *fixes* the disk pressure and you keep the free 2×
  margin. This is what flips ADR-0004's `PUBLISH` capacity gate to `true`.
- **Law 3 simplified, not just satisfied.** `automation/01-system-files-overlay.sh`
  still symlinks every `.container` into `bound-images.d/`, but all now resolve to
  **one or two image IDs**; bootc dedups by image ID, so the store holds
  `mios-sys` (+`mios-cuda`) instead of 18 upstreams.
- **Law 6 untouched by construction.** Every `User=`/`Group=`/`Delegate=yes` and
  each `[security.privileged_quadlets].root` exception (ceph/k3s/pxe/runner) is
  byte-identical. Consolidation is image-side only.
- **Law 7/8 hold.** Quadlets stay generated, never hand-edited;
  `Image=`/`Exec=` are projected from `[containers.*]` and drift-gated. A
  hand-edited `Exec=` fails the gate.
- **Rollback safety improves.** `mios-sys`/`mios-cuda` live inside the versioned
  OCI layers under immutable `/usr/lib/containers/storage`, so a `bootc rollback`
  reverts OS + baked binaries + Quadlets **atomically** as one image swap — no
  partial state, no runtime re-pull.
- **Precedent:** apko/Wolfi single-base discipline (Chainguard's whole catalog on
  one `wolfi-base` — adopt the discipline, keep the host's base for NVIDIA
  parity), Google distroless (app+runtime only as thin leaves), Bitnami minideb
  (one glibc base, many runtimes — the closest precedent, and why glibc over musl),
  and MiOS's own in-tree `localhost/*` images.

## Alternatives considered

- **Sidecars reference host `/usr` via `additionalimagestores` (operator's
  original mechanism).** Rejected on fact: ostree/composefs host `/usr` and the
  containers-storage overlay additional store do not blob-share. The equivalent
  win comes from single-base-within-the-store.
- **One image (not two), CUDA as an internal shared layer.** Rejected: forces a
  ~12 GB CUDA rootfs onto small services (a socat container must not carry CUDA).
  The two-image split keeps CUDA only in `mios-cuda` at near-zero extra store cost
  (shared base dedups across the two).
- **Wolfi/apko as the base.** Rejected: shares zero layers with ucore-hci and
  lacks the NVIDIA driver userspace → re-adds gigabytes of CUDA and breaks
  base-parity/CDI. Adopt apko's *discipline*, keep the host's base.
- **`FROM localhost/mios:latest` (derive from the OS itself).** Rejected —
  chicken-and-egg: the OS image is not yet committed inside its own Containerfile
  and `/usr` is immutable post-commit. `FROM ${BASE_IMAGE}` gives functionally the
  host's base userspace with no build-ordering hazard.
- **One shared venv for vLLM+SGLang.** Rejected: divergent torch pins conflict.
  Share the CUDA/torch *base layer*, keep two sibling venvs.
- **Model B (s6/supervisord fat container).** Rejected — see Decision.

## Consequences

Positive — the size win (`SIZE` table, from the design study):

| store | current | optimistic | conservative |
|---|---|---|---|
| Bound-image store | ~60 GB | ~21 GB | ~27–30 GB |
| — of which CUDA/torch | baked ~2.5× (~24 GB) | one L2, ~9 GB | ~12 GB |
| Compressed push (~84 GB img today →) | ~30–35 GB | — | ~18–20 GB |
| Day-2 `bootc upgrade` delta | multi-GB/sidecar bump | — | sub-GB shared-layer delta |

Standard GitHub `ubuntu-24.04` (~66 GB usable): current monolith 60 GB × 2–3× =
120–180 GB → `exit 125`; MiOS-Sys + Phase-0 sharding = largest commit ≈ 12 GB × 3×
= **36 GB, comfortable** — the safe configuration that lets a standard runner bake.

Negative / honest costs:
- **CVE ownership shifts to MiOS.** Consolidation forfeits each upstream's
  independent patch stream; a shared-base CVE hits every baked service.
  Mitigations: digest-lock every ingredient (ADR-0003), a `MIOS_<X>_VERSION` key
  per app read by a fetcher that verifies `checksums.txt`/`.asc` (the
  `automation/38-llamacpp-prep.sh` mold), weekly + on-CVE rebuilds, cosign-sign +
  bootc-verify + grype/trivy on **two** base surfaces instead of eighteen.
- **musl/glibc skew.** alpine-lineage services (socat, redis, searxng) are musl;
  we **rebuild** via Fedora RPM or pip venv, never `COPY --from` a musl binary
  onto glibc. Static Go binaries are libc-agnostic and lift cleanly.
- **Package-availability gaps** to resolve at implementation: CrowdSec has no
  F41+/F44 RPM (use the static release tgz); F41 dropped Redis (use `valkey`,
  wire-compatible); Fedora packages pgvector for PG16/PG18 **not PG17** (source-
  build 0.8.3 against PG17 to preserve the `0.8.3-pg17` pin + `hnsw.iterative_scan`
  guarantee).

Status — **PLANNED** (waved). The Quadlet-generation delta is a **pure SSOT edit,
no generator code change**: `tools/generate-pod-quadlets.py`'s
`render_nested_quadlet()` already passes `Image=`/`Exec=` through verbatim. Per
folded member, exactly two lines change — `Image=` → `${MIOS_SYS_IMAGE}` (or
`${MIOS_CUDA_IMAGE}`) and a new `Exec=` (the entrypoint the upstream image made
implicit). `User=`/`Group=`/`Delegate=`/`[Unit]`/`Condition*` stay byte-identical.

Two-gate reconciliation (ADR-0001): the **BAKE gate** now means "present in
`mios-sys`/`mios-cuda`" (CORE, unconditional presence); the **ACTIVATION gate**
(each Quadlet's per-blade `Condition*`) is **completely unchanged** — a WSL blade
still won't start pxe-hub even though its binary is now baked. Clean orthogonality.

## Implementation

Wave 0 — wiring (one-time, no service moves):
- `usr/share/mios/mios.toml` — add `[image.sys]` + `[image.cuda]` (`image`,
  `base = "${BASE_IMAGE}"`, `core = [...]`, `[image.sys.layers]` = python/node/jre,
  `[image.cuda.layers]` cuda = [torch, flashinfer, xformers, triton]); add
  `sys`/`cuda` refs to `[image.sidecars]` (~L7806).
- Add `MIOS_SYS_IMAGE` + `MIOS_CUDA_IMAGE` to the `userenv.sh` slot map **and to
  BOTH allowlists** in `automation/34-render-quadlets.sh` — the `envsubst`
  arg-string (line 73) and the bash-fallback loop (~L87–127);
  `automation/97-ssot-lint.sh` fails the build if either end is missing (Law 7/8).
- `automation/57-mios-sys-build.sh` (new) — builds both images **into**
  `/usr/lib/containers/storage`, `--network=host --layers --mount=type=cache`,
  verify-or-fail-loud (mirrors `52–56-bake-*.sh` + the `72-hermes-agent.sh` venv /
  `38-llamacpp-prep.sh` checksum molds). Generated Containerfiles at
  `usr/share/mios/sys/Containerfile` and `usr/share/mios/cuda/Containerfile`.
- `C:\MiOS\Containerfile` (~180–190) — replace the five `mios-bake-group` RUNs
  with the `57-mios-sys-build.sh` RUN (+ a residual "extra" group for still-
  upstream images); `ostree container commit` + `bootc container lint` untouched.
  `[build].bake_groups` evolves `["vllm","sglang","ai","infra","extra"]` →
  `["sys","cuda","extra"]`. Retire `mios-bake-group`'s `use_hard_links` path
  (superseded by dedup-by-construction).

Wave 1 — Go/static-binary tier (biggest win, lowest risk):
socat (Fedora RPM), AdGuard, Matchbox, Forgejo, Jaeger (v2 single binary +
config port), CrowdSec (static tgz). One binary each into `mios-sys`; repoint
`Image=`, add `Exec=`; regenerate via `automation/14-generate-quadlets.sh`.

Wave 2 — interpreted/native + self-contained privileged binaries:
SearXNG, Open WebUI, code-server, redis→valkey, guacd; fold
`mios-crawl4ai-slim` + `mios-firecrawl` onto shared L1/L1b/chromium (two
`localhost/*` images disappear); then k3s + forgejo-runner binaries (privileged
activation unchanged).

Wave 3 — CUDA base + reproducibility-sensitive (gated on a data-plane smoke test):
build `mios-cuda` (L2 whale + `vllm-venv` + `sglang-venv` + `llama-server` — the
~12–13 GB byte win); then postgres+pgvector (one instance, resolve the PG17-pgvector
gap) and guacamole-web (JRE/Tomcat WAR — the weakest case, KEEP-SEPARATE acceptable).

Never: **Ceph** (`quay.io/ceph/ceph:v19`) — upstream is container-only via
cephadm, Fedora packaging is effectively dead, multi-daemon, root + raw block
devices, cluster-version-locked. The one pulled image.

Open questions for the operator (recorded, not blocking): whether `mios-cuda` is
core-baked into *every* blade image or gated to GPU classes at the bake-manifest
level; PG16-realign vs PG17 source-build for pgvector; JRE-in-`mios-sys` vs
KEEP-SEPARATE for guacamole-web; k3s binary vs upstream tracking; rebuild cadence;
digest-locking the floating `:latest` sources (hermes, guacamole, crowdsec,
postgres, guacd, pxe_hub) as part of Wave 0.

## References

- apko / Wolfi single-base discipline: <https://github.com/chainguard-dev/apko>
- Google distroless: <https://github.com/GoogleContainerTools/distroless>
- Bitnami minideb (one glibc base, many runtimes): <https://github.com/bitnami/minideb>
- s6-overlay (the Model-B path we reject for cross-app packing):
  <https://github.com/just-containers/s6-overlay>
- bootc bound images + containers-storage `additionalimagestores`:
  <https://bootc.dev/bootc/logically-bound-images.html>,
  <https://github.com/containers/storage/blob/main/docs/containers-storage.conf.5.md>
- Ceph cephadm (container-only → the KEEP-SEPARATE case):
  <https://docs.ceph.com/en/latest/cephadm/>
- Sibling ADRs: ADR-0001 (two-gate model; the bake groups this consolidates),
  ADR-0003 (digest-lock ingredients as SBOM), ADR-0004 (shrinking the bake flips
  the `PUBLISH` capacity gate).
