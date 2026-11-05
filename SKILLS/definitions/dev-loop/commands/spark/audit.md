---
description: Brownfield repository inspector and hierarchical AGENTS.md context generator
argument-hint: [repo_dir]
---

# /audit: Brownfield Repository Audit for Gemini Spark

Inspect target repository structures, detect build systems and linters, and generate project-native contracts.

## Execution Protocol
1. Run audit inspection engine: `python3 reference/audit.py ${ARGUMENTS:-.}`.
2. Extract project invariants, active test suites, and package definitions.
3. Generate or synchronize hierarchical `AGENTS.md` operational contracts defining disjoint worktree partitions.
