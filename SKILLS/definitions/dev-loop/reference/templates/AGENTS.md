# Multi-Agent Collaboration Contracts & Guidelines

## 1. File Partitioning Law
- Agents working concurrently MUST operate in strictly disjoint file sets.
- If two lanes require changes to the same file, changes must be sequenced through an L1 Supervisor.

## 2. Git Staging Mandate
- NEVER run `git add -A`, `git add .`, or directory adds.
- Stage only explicit file paths: `git add src/module/feature.py`.

## 3. Collaborator Non-Interference
- Never delete or revert untracked or uncommitted files created by another agent or human collaborator.
- Always run `git status` and `git log -1 --stat` before concluding a file is obsolete.

## 4. Well-Formedness Checks
- Before proposing task completion, run:
  - Python: `python3 -m py_compile <file>`
  - Shell: `bash -n <file>` && `wc -c <file>` (> 0)
  - TypeScript: `tsc --noEmit`
