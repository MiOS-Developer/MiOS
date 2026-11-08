# /goal Prompt for OpenAI Codex

Define and enforce autonomous goal lifecycles with stopping conditions:

1. **Goal-Driven Development (`/goal dev <objective>`):**
   - Initialize `GOALS.md` defining objective, non-goals, and concrete stopping criteria.
   - Decompose into `tasks.jsonl`.
   - Run iterative execution turns in isolated git worktrees (`.worktrees/<worker_id>`).
   - Run `python3 reference/goal.py eval` after each turn. Loop until ALL criteria pass.
2. **Invariants:**
   - Enforce two-sided verification: positive control passes, negative control fails as expected.
   - Atomic writes (`.tmp` -> rename); zero-byte truncations prohibited.
   - Reconcile with atomic merge gates (`git merge --no-ff`, conflict rollback via `git merge --abort`).
