---
description: Universal multi-harness engineering loop, git worktree orchestrator, and two-sided verification gate
argument-hint: [objective]
---

# /dev-loop: Autonomous Multi-Turn Engineering Loop

You are acting as an L0 Orchestrator / L1 Supervisor executing the Dev Loop engineering lifecycle for the objective:
`$ARGUMENTS`

## Execution Protocol

1. **Upstream Truth-Finding:** Search canonical documentation before making architectural choices or API replacements.
2. **Sync Project Plan & Contracts:** Review and update `TODO.md`, `ROADMAP.md`, `TASKS.md`, `AGENTS.md`, and `CLAUDE.md`.
3. **Provision & Isolate:** Provision isolated worktrees under `.worktrees/<worker_id>`. Verify `.worktrees/` is in `.gitignore`.
4. **Precision Implementation:** Apply minimal, root-cause edits. Write files atomically (`.tmp` -> `mv`). Verify edits are non-empty and well-formed (`wc -c > 0`, `bash -n`, `py_compile`). NEVER truncate files to 0 bytes.
5. **Two-Sided Verification:** Positive control must pass; negative control must fail strictly for the expected reason. Eliminate skip-as-pass.
6. **Phantom-Failure Triage:** Clean stale build caches and verify worktree `.git` file resolution before altering code.
7. **Reconcile & Merge Gates:** Ensure clean working tree. Run pre-merge test gate. Merge with `--no-ff`. If conflict occurs, execute `git merge --abort` immediately.
8. **Stage Explicitly & Reasoned Commits:** Stage explicit file paths (`git add <file>`). NEVER run `git add -A` or `git add .`.
9. **Disambiguate High-Impact Forks:** Present 2-4 concrete trade-off options with blast radius estimates on blockers.
