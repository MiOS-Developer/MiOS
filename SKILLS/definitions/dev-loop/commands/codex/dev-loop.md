# Dev Loop Orchestrator Prompt for OpenAI Codex

When executing engineering objectives under the `/dev-loop` workflow:

1. **Architecture & Upstream Truth:** Query authoritative docs before modifying APIs.
2. **Worktree Isolation:** Execute edits in dedicated worktrees (`.worktrees/<worker_id>`). Treat `.git` as a file in linked worktrees.
3. **Atomic Precision:** Write files atomically. Validate syntax and size (`wc -c > 0`). Zero tolerance for 0-byte truncations.
4. **Hard Two-Sided Verification:** Validate positive control (feature passes) and negative control (defect caught).
5. **Git Discipline:** Stage explicit file paths (`git add <file>`). Never run `git add -A`. Write reasoned commit messages.
6. **Phantom Triage:** Isolate cache and environment artifacts before modifying code.
