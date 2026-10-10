---
description: SCOPE staged code review, contract preservation, and security audit
argument-hint: [target_ref]
---

# /review (alias /rv): SCOPE Staged Code Review

Audit codebase diffs against contract specifications, invariants, and security policies.

Usage:
`/review [git_ref]`
Alias: `/rv [git_ref]`

## Protocol
1. Run staged audit: `python3 reference/review.py $ARGUMENTS`.
2. Inspect for credential leaks, unsafe shell commands, and contract drift.
3. Block merge on critical findings; generate `REVIEW.md`.
