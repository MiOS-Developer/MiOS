---
description: Deep upstream repository research, release diffing, and breaking change analysis (alias /rs)
argument-hint: [base_ref] [upstream_ref]
---

# /research (alias /rs): Upstream Repository Research for Gemini Spark

Investigate upstream repository diffs, breaking changes, CVE disclosures, and dependency updates before altering code.

## Execution Protocol
1. Analyze repository diffs: `python3 reference/research.py diff ${ARGUMENTS}`.
2. Cross-reference canonical documentation and security advisories using `google:search` and `context_service_agent:get_context`.
3. Scaffold formal research brief templates: `python3 reference/research.py template <spike|upstream|eval>`.
4. Export findings to `UPSTREAM_BRIEF.md` and persist research reports directly to Google Drive.
