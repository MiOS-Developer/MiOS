---
name: pipeline
description: Execute sequential MiOS CI/CD pipeline verification including environment checks, SSOT integrity, test suites, credential audits, and projection synchronization.
---

# MiOS Pipeline Execution Workflow (AGY)

This workflow defines the sequential pipeline verification and build cycle for Antigravity agents operating in MiOS CI/CD automation.

## Pipeline Steps

### Step 1: Pre-Flight Environment Validation
- Confirm container or host environment: `bootc`, `podman`, `systemd`, `rustc`, `cargo`.
- Confirm `MIOS_AI_ENDPOINT` is reachable and OpenAI-API compliant.
- Ensure Linux keyring daemon (`gnome-keyring` or `secret-tool`) is available for credential retrieval.

### Step 2: SSOT Integrity & Phase Registry
- Validate `usr/share/mios/mios.toml` syntax:
  ```bash
  python3 -c "import tomllib; tomllib.load(open('usr/share/mios/mios.toml', 'rb'))"
  ```
- Verify phase registry and ratchet ceilings:
  ```bash
  src/mios-rs/target/debug/mios-gate phase-registry --root /workspaces/MiOS
  src/mios-rs/target/debug/mios-gate ratchet-direction --root /workspaces/MiOS
  ```

### Step 3: Test Suites & Regression Verification
- Run registered test suites:
  ```bash
  python3 tools/ci-suites.py --check
  ```
- Run targeted tier 1 tests for changed domains.

### Step 4: Security & Credentials Audit
- Enforce zero credential literals in system units and scripts:
  ```bash
  src/mios-rs/target/debug/mios-gate credential-literals --root /workspaces/MiOS
  src/mios-rs/target/debug/mios-gate version-literals-ssot --root /workspaces/MiOS
  src/mios-rs/target/debug/mios-gate signature-policy --root /workspaces/MiOS
  ```

### Step 5: Total Projection Synchronization
- Re-project all downstream targets from the SSOT:
  ```bash
  bash ./tools/sync-generated.sh
  ```
- Assert that no unstaged or unsynchronized diffs exist:
  ```bash
  git status --porcelain
  ```
