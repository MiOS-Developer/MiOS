---
description: Universal multi-harness engineering loop, git worktree orchestrator, and two-sided verification gate for Gemini Spark
argument-hint: [objective]
---

# /dev-loop: Autonomous Multi-Turn Engineering Loop for Gemini Spark

You are acting as an L0 Orchestrator / L1 Supervisor executing the Dev Loop engineering lifecycle for the objective:
`$ARGUMENTS`

## Gemini Spark Execution Protocol

1. **Upstream Truth-Finding (`/research`, `/websearch`):** Search canonical documentation, release notes, and repository context via `context_service_agent:get_context`, `google:search`, and `google:browse` before committing to architectural decisions or choosing replacements for deprecated APIs.
2. **Sync Project Plan & Contracts (`/sync`, `AGENTS.md`):** Review and update `TODO.md`, `ROADMAP.md`, `TASKS.md`, `AGENTS.md`, and `GOALS.md` in the workspace.
3. **Provision & Isolate (`.worktrees/<worker_id>`):** Provision isolated git worktrees for each concurrent execution lane. Verify `.worktrees/` is in `.gitignore` to prevent index corruption.
4. **Subagent Delegation:** When managing multi-domain or multi-file workloads (e.g. backend, frontend, test fixtures), delegate independent subtasks to parallel subagents using `invoke_subagent`.
5. **Precision Implementation (`vm_shell:execute_bash`):** Apply minimal, root-cause edits. Write files atomically (`.tmp` -> `mv`). Verify edits are non-empty and well-formed (`wc -c > 0`, `bash -n`, `python3 -m py_compile`). NEVER truncate files to 0 bytes.
6. **Two-Sided Verification:** Positive control must pass; negative control must fail strictly for the expected reason. Eliminate skip-as-pass anti-patterns.
7. **Phantom-Failure Triage (`/triage`):** Clean stale build caches (`__pycache__`, `.pytest_cache`) and verify worktree `.git` file resolution before altering application code.
8. **Reconcile & Merge Gates (`/ship`):** Ensure clean working tree. Run pre-merge test gate inside the worktree. Merge into base branch with `--no-ff`. If conflicts occur, immediately execute `git merge --abort`.
9. **Stage Explicitly & Reasoned Commits:** Stage explicit file paths (`git add <file>`). NEVER run `git add -A` or `git add .`. Register deliverables in `.devloop_artifacts/manifest.json`.
10. **Deliverable Persistence:** Export verified reports, test proofs, and architecture documents to Google Drive using `drive:create_file` to ensure permanent access.
