---
description: State and context synchronization keeping docs aligned with git commits
argument-hint: []
---

# /sync: State & Context Synchronization for Gemini Spark

Reconcile `AGENTS.md`, ADR statuses, and project tracking files against live git commits.

## Execution Protocol
1. Run synchronization engine: `python3 reference/sync.py`.
2. Verify that documented contracts reflect recent commits and branch updates.
3. Prune stale tracking entries and update `progress.md` state spine.
