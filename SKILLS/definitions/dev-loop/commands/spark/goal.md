---
description: Define, anchor, and autonomously execute engineering goals with strict stopping conditions for Gemini Spark
argument-hint: [dev] [objective]
---

# /goal: Goal-Driven Autonomous Engineering Engine for Gemini Spark

You are executing a goal-oriented engineering workflow for the input:
`$ARGUMENTS`

## Operating Directives

1. **Subcommand Routing:**
   - When invoked as `/goal dev <objective>`, initiate full **Goal-Driven Development**:
     a. Initialize `GOALS.md` with explicit, verifiable stopping conditions and acceptance invariants.
     b. Decompose acceptance criteria into `tasks.jsonl`.
     c. Launch the `/dev-loop` heartbeat orchestrator across isolated git worktrees (`.worktrees/<worker_id>`).
     d. Iterate until `python3 reference/goal.py eval` confirms all stopping conditions pass.
     e. Execute atomic merge gates, verify two-sided tests, and commit with cryptographic artifact proofs.
   - Otherwise, evaluate or display the current repository goal stopping conditions (`python3 reference/goal.py status`).

2. **Stopping Condition Discipline:**
   - Never consider a task complete merely because a command exited with code 0.
   - All acceptance invariants (positive controls) must pass, and negative controls must prove failure on defect.
   - Zero linter warnings, zero regressions, and manifest SHA-256 validation are required.

3. **Blast Radius Limits:**
   - Define non-goals explicitly in `GOALS.md`. Restrict file modifications strictly to the designated subsystem.
