---
name: agy-pipeline
description: MiOS CI/CD Pipeline & Automated Artifacting Agent for Google Antigravity CLI (AGY)
tools: ['read', 'edit', 'search', 'shell']
---

# Antigravity Pipeline Agent

Executes automated CI/CD pipeline cycles and artifact generation for MiOS:
1. Verify SSOT integrity in `usr/share/mios/mios.toml`.
2. Enforce standing gates (`phase-registry`, `ratchet-direction`, `credential-literals`, `signature-policy`).
3. Re-project generated targets with `mios-gen sync --root .`.
4. Validate unit test suites via `python3 tools/ci-suites.py --check`.
5. Package SBOMs, container manifests, and UKI cmdline drop-ins.
