---
name: artifacting
description: Automated artifact generation and packaging workflow for Antigravity CLI (AGY) in MiOS CI/CD release cycles.
---

# MiOS Artifacting Workflow (AGY)

This workflow defines the generation, verification, and packaging of deployment artifacts by Antigravity agents in CI/CD release cycles.

## Artifact Targets

### 1. Unified Kernel Image (UKI) Command-Line
- Generate commandline drop-ins from `usr/share/mios/kargs.d/`:
  - `boot/loader/entries/` and baked UKI parameters.
  - Invariant: Kernel args are cryptographically signed into the UKI, distinct from MOK keys.

### 2. Software Bill of Materials (SBOM) & Container Signatures
- Generate SPDX and CycloneDX SBOM manifests:
  ```bash
  automation/92-export-sbom.sh
  ```
- Generate container signature policy in `usr/lib/containers/policy.json` aligned with `[security.sigstore]` in `mios.toml`.

### 3. FHS System Projections & Native Manpages
- Render native roff manpages into `usr/share/man/` (verified via `tools/sync-generated.sh`).
- Render flat ports mapping and `automation/lib/globals.{sh,ps1}`.
- Update `usr/share/mios/reference/manual-corpus.tsv` to ensure complete corpus tracking.

### 4. Test Receipts & Task Ledger Persistence
- Consolidate test run receipts into `.devloop/run-*/report-*.json`.
- Update `.devloop/LEDGER.md` with durable evidence, commit hashes, and verification status.
