# HARNESS.md - Permanent Outer-Harness Failure Ratchet Log

Every agent mistake that escapes in-loop fast-clock recovery is permanently codified
into this ledger to guarantee it cannot recur in subsequent autonomous runs.

## Codified Outer-Harness Rules

| Rule ID | Category | Enforcement Hook | Description | Date Added |
| :--- | :--- | :--- | :--- | :--- |
| `fence/path-traversal` | PATH_ESCAPE | PreToolUse | Block any file path resolving outside repository root | 2026-09-19 |
| `fence/branch-protection` | M-CPE | PreToolUse | Bar direct git pushes to main or release branches | 2026-09-19 |
| `gate/value-provenance` | AST_INVALIDATION | PreToolUse | Enforce declared provenance sources for all returned fields | 2026-09-19 |
