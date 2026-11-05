---
description: SCOPE staged code review, security audit, and contract preservation (alias /rv)
argument-hint: [target_ref]
---

# /review (alias /rv): SCOPE Staged Code Review for Gemini Spark

Audit codebase diffs against contract specifications, invariants, and security policies.

## Execution Protocol
1. Execute review engine: `python3 reference/review.py ${ARGUMENTS:-HEAD}`.
2. Inspect for credential exposures, unsafe shell evaluations, and breaking contract drift.
3. Verify that all modified files are non-empty and syntactically valid.
4. Export audit report to `REVIEW.md`. Block merge on critical findings.
