---
description: Automated phantom-failure diagnosis, flakiness detection, and minimal repro generation
argument-hint: [test_command]
---

# /triage (alias /tr): Phantom-Failure & Flakiness Diagnostic Engine

Classify test and build failures into deterministic bugs, flaky runs, or environment locks.

Usage:
`/triage "<command>"`
Alias: `/tr "<command>"`

## Protocol
1. Executes the failing command across multiple iterations: `python3 reference/triage.py "$ARGUMENTS"`.
2. Differentiates `INDEX_LOCK`, `RESOURCE_STARVATION`, `FLAKY_TEST`, and `DETERMINISTIC_BUG`.
3. Emits `repro.sh` and logs diagnostics to `TRIAGE.md`.
