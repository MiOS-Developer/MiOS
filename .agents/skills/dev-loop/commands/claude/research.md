---
description: Deep upstream repository research, release diffing, and breaking change analysis
argument-hint: [base_ref] [upstream_ref]
---

# /research (alias /rs): Upstream Dependency & Architecture Research

Analyze upstream changes before modifying codebase contracts.

Usage:
`/research <base_tag_or_sha> [upstream_tag_or_sha]`
Alias: `/rs <base> [upstream]`

## Execution Protocol

1. Query upstream git changes using `python3 reference/research.py diff $ARGUMENTS`.
2. Inspect deleted files, changed signatures, and deprecated modules.
3. Generate `UPSTREAM_BRIEF.md` detailing breaking changes and necessary architectural adaptations.
