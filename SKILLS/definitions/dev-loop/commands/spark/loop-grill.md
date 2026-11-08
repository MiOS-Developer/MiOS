---
description: Socratic issue grilling and criteria hardening before implementation
argument-hint: [issue_id_or_spec]
---

# /loop-grill: Socratic Issue Grilling & Refinement for Gemini Spark

Interrogate requirements, harden acceptance criteria, and establish two-sided test strategies before implementation begins.

## Execution Protocol
1. Transition state machine: `python3 reference/lifecycle.py set refinement`.
2. Extract implicit assumptions, ambiguous constraints, and operational failure modes.
3. Define concrete non-negotiable invariants, positive controls, and negative controls.
4. If ambiguous forks exist, present 2-4 concrete trade-off options with blast radius estimates to the operator.
