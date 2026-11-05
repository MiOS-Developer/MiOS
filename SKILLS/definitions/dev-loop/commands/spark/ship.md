---
description: Safe trunk-based branch merger, worktree pruner, and changelog updater (alias /sh)
argument-hint: [source_branch] [target_branch]
---

# /ship (alias /sh): Trunk-Based Shipping Engine for Gemini Spark

Reconcile completed feature lanes into the trunk branch atomically with merge gates.

## Execution Protocol
1. Run shipping engine: `python3 reference/ship.py ${ARGUMENTS}`.
2. Verify pre-merge status (`git status --porcelain` is clean).
3. Execute pre-merge test gate inside the lane worktree.
4. Merge into target branch using `--no-ff`. On conflict, immediately execute `git merge --abort`.
5. Prune lane worktree (`git worktree remove`) and delete feature branch.
6. Record release entry in `CHANGELOG.md` and update `.devloop_artifacts/manifest.json`.
