---
description: Define, anchor, and autonomously execute engineering goals with strict stopping conditions
argument-hint: [dev] [objective]
---

# /goal: Goal-Driven Autonomous Engineering Engine

You are executing a goal-oriented engineering workflow for the input:
`$ARGUMENTS`

## Operating Directives

1. **Subcommand Routing:**
   - If the first argument is `dev` (e.g. `/goal dev <objective>`), initiate full **Goal-Driven Development**:
     a. Initialize `GOALS.md` with explicit stopping conditions and acceptance invariants.
     b. Decompose into `tasks.jsonl`.
     c. Launch the `/dev-loop` heartbeat orchestrator across isolated git worktrees.
     d. Iterate until `reference/goal.py eval` confirms all stopping conditions pass.
     e. Execute atomic merge gates and commit with cryptographic artifact proofs.
   - Otherwise, initialize or evaluate the current repository goal stopping conditions.

2. **Stopping Condition Rule:**
   - Never consider a task done merely because an execution command completed.
   - The loop MUST evaluate both positive controls (acceptance test passes) and negative controls (defect is caught).
   - Invariants must be satisfied before exiting.

3. **Blast Radius Limits:**
   - Define non-goals explicitly in `GOALS.md`. Do not touch files outside the task scope.
