---
description: Automated phantom-failure diagnosis and flaky test detection (alias /tr)
argument-hint: [failing_command]
---

# /triage (alias /tr): Phantom-Failure Triage for Gemini Spark

Diagnose failing tests, isolate environment/cache artifacts from real regressions, and generate standalone reproduction scripts.

## Execution Protocol
1. Execute triage engine: `python3 reference/triage.py "$ARGUMENTS" --runs 3`.
2. Follow the 5-step triage runbook:
   - Check local vs CI path mismatches.
   - Invalidate stale build artifacts (`__pycache__`, `.pytest_cache`).
   - Verify linked worktree `.git` file pointer resolution (`git rev-parse --git-dir`).
   - Isolate root failure from cascading errors.
   - Synchronize build manifests and catalogs.
3. Generate minimal reproduction script (`./repro.sh`) and log findings in `TRIAGE.md`.
