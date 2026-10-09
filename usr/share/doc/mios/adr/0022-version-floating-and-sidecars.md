<!-- AI-hint: ADR 0022: Version Floating, Image Sidecars, and Offline Vendoring Policy.
     AI-related: usr/share/mios/mios.toml, usr/share/doc/mios/adr/0012-float-latest-no-hand-pinned-versions.md, usr/share/doc/mios/adr/README.md -->
---
adr: 0022
title: "Version floating, image sidecars, and offline vendoring policy"
status: superseded
date: 2026-08-02
deciders: [operator, ai-pair]
tags: [float-latest, sbom, sidecars, k3s, offline-vendoring]
laws: [7, 8, 12]
ssot_keys: [image.sidecars, versions]
related_ws: [WS-SBOM, WS-MIOSSYS]
supersedes: []
superseded_by: [0012]
---

# ADR-0022: Version floating, image sidecars, and offline vendoring policy

## Status

superseded — 2026-08-02. Originally recorded as `docs/adr/0004-version-floating-and-sidecars.md`; promoted to canonical ADR namespace under task_958. Formalized and generalized across all artifact classes by ADR-0012.

## Context

MiOS requires reproducible builds while supporting floating upstream images and dependencies where appropriate. Certain services (such as `rancher/k3s`) are vendored offline for air-gapped installation and require explicit SSOT version tracking, whereas registry-pulled sidecars express `:latest` (or floating major version) intent.

## Decision

1. **Dynamic Image Floating**: Sidecars pulled dynamically at runtime use floating tags (e.g., `:latest` or `:v19`) in `mios.toml [image.sidecars]`. Resolved `@sha256` digests are recorded into the SBOM manifest at build time (`automation/90-generate-sbom.sh` / `MiOS-SBOM.csv`).
2. **Offline Vendored Sidecars Exception**: Components required for offline bootstrap (such as `rancher/k3s`) pin exact upstream tags in `[image.sidecars]` and `[versions]`. Any version bump flows through SSOT (`mios.toml`) -> offline re-vendoring -> build manifest update.
3. **No Hand-Typed Digests**: No `@sha256:` hashes are hand-pinned in `mios.toml`; digests are strictly resolved during the OCI build step.

## Rationale

Air-gapped and reproducible deployments require deterministic components where network access is absent, while runtime sidecars benefit from floating tags with build-time digest recording into SBOM to avoid manual hash churn and silent drift.

## Consequences

- Sidecars floating tags resolve to SBOM digests at build time.
- Offline vendored dependencies require explicit SSOT updates.
- Generalized across all artifact classes (RPMs, git clones, model weights) by ADR-0012.
