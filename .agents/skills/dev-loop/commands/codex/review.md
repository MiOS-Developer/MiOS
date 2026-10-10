# /review (alias /rv) Prompt for OpenAI Codex

Audit code diffs against the SCOPE staged review model:
- Run: `python3 reference/review.py [ref]`
- Scans for secrets, shell injection vulnerabilities, and contract violations.
- Emits findings to `REVIEW.md`.
