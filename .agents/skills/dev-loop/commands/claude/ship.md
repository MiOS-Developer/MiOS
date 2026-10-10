---
description: Safe trunk-based branch merge, worktree pruning, and changelog update
argument-hint: [source_branch] [--target main] [--tag vX.Y.Z]
---

# /ship (alias /sh): Trunk-Based Release & Worktree Reconciliation

Automate safe merges into main, prune worktrees, and record release notes.

Usage:
`/ship <source_branch> [--target main] [--tag vX.Y.Z]`
Alias: `/sh <source_branch>`

## Protocol
1. Validate pre-flight checks (clean git status, passing tests, passed review).
2. Execute merge: `python3 reference/ship.py $ARGUMENTS`.
3. Reconcile branch, prune ephemeral worktree, and append entry to `CHANGELOG.md`.
