---
description: Real-time documentation lookup, API verification, and error triage (alias /s)
argument-hint: [query]
---

# /websearch (alias /s): Real-Time Documentation & Error Discovery for Gemini Spark

Search authoritative vendor documentation, specifications, and issue trackers.

## Execution Protocol
1. Run local research search engine: `python3 reference/research.py search "$ARGUMENTS"`.
2. Augment with live web queries via `google:search` and `google:browse`.
3. Ground all API contracts, parameter names, and deprecation timelines before generating code.
