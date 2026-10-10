# /research (alias /rs) Prompt for OpenAI Codex

Investigate upstream repository diffs and breaking changes between git tags or commits:
- Run: `python3 reference/research.py diff <base> [upstream]`
- Extract deleted modules, altered interfaces, and updated invariants.
- Export findings to `UPSTREAM_BRIEF.md`.
