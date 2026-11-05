---
name: dev-pipeline
description: Drive MiOS core operating system pipelines, gate audits, and automated artifacting cycles.
---

# MiOS Dev-Pipeline Skill

Provides procedures for Antigravity agents executing development, CI/CD, and artifacting cycles on MiOS.

## Procedures

### 1. Gate Audit
Run the suite of standing gates:
```bash
src/mios-rs/target/debug/mios-gate phase-registry --root /workspaces/MiOS
src/mios-rs/target/debug/mios-gate ratchet-direction --root /workspaces/MiOS
src/mios-rs/target/debug/mios-gate credential-literals --root /workspaces/MiOS
src/mios-rs/target/debug/mios-gate version-literals-ssot --root /workspaces/MiOS
src/mios-rs/target/debug/mios-gate signature-policy --root /workspaces/MiOS
python3 tools/ci-suites.py --check
```

### 2. Unit Test Verification
Run affected tests and ensure two-sided controls (positive control passes, negative control rejects incorrect behavior):
```bash
python3 tests/<test-name>.py
```

### 3. Total SSOT Synchronization
```bash
bash ./tools/sync-generated.sh
git diff --exit-code
```
