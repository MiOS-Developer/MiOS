---
description: Targeted web discovery for canonical vendor docs, API specs, and error triage
argument-hint: [query]
---

# /websearch (alias /s): Real-Time Documentation & Error Discovery

Search authoritative vendor documentation, specifications, and issue trackers.

Usage:
`/websearch <query>`
Alias: `/s <query>`

## Execution Protocol

1. Run `python3 reference/research.py search "$ARGUMENTS"`.
2. Cross-reference local docs and canonical web sources.
3. Output relevant code samples and API contract definitions before writing code.
