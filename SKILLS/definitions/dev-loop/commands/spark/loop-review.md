---
description: Multi-perspective critic panel review (DRY, KISS, SRP, Security)
argument-hint: [target_ref]
---

# /loop-review: Multi-Perspective Critic Panel for Gemini Spark

Convene an independent maker-checker review panel to audit staged or committed changes.

## Execution Protocol
1. Transition state machine: `python3 reference/lifecycle.py set pre_approval_gate`.
2. Execute the multi-perspective critic engine: `python3 reference/critic.py ${ARGUMENTS:-HEAD}`.
3. Review code across four independent dimensions:
   - **DRY:** Identify redundant abstractions and duplicate implementations.
   - **KISS / YAGNI:** Strip unnecessary complexity and speculative generalizations.
   - **SRP / SoC:** Verify strict single-responsibility boundaries and modularity.
   - **Security & Invariants:** Audit memory safety, unprivileged execution, shell sanitization, and credential leaks.
4. Block merge if any critical findings or unhandled regressions are identified.
