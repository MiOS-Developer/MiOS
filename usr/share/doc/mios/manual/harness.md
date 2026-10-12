<!-- AI-hint: Manual pages distilled from the source comments of harness, sanitized, each passage anchored to the comment it came from. -->

# harness

### MiOS embedded harness verification gate evaluator....

MiOS embedded harness verification gate evaluator.

Discovers `invariants/test_*.sh` scripts and executes each one, instead of
merely counting how many exist. Exit-code contract for invariant scripts:

    0   PASS  - invariant verified on this host/container.
    2   SKIP  - invariant is not applicable here (declared optional hardware
                or capability is absent); NOT counted as a pass.
    any other non-zero - FAIL.

The gate fails closed: zero discovered scripts, zero executed scripts, or an
all-SKIP run (nothing actually verified) all exit non-zero. This avoids the
"no tests found -> declared success" and "skip-as-pass" failure modes.

<!-- mios-src:39b79bed2e84 from harness/verification_gates.py:2-15 -->

### critic.py - Multi-Perspective Maker-Checker Critic Panel...

critic.py - Multi-Perspective Maker-Checker Critic Panel Engine.
Evaluates diffs prior to merge across four specialized architectural lenses:
- DRY Critic (Duplication & Reuse)
- KISS & YAGNI Critic (Simplicity & Over-engineering)
- SRP & SoC Critic (Separation of Concerns & Modularity)
- Security & Invariant Critic (Vulnerabilities & Schema Contracts)

<!-- mios-src:cfa7e78b2956 from .agents/skills/dev-loop/reference/critic.py:2-9 -->

### !/usr/bin/env bash...

!/usr/bin/env bash
==============================================================================
install.sh - Universal Installer for Dev Loop Suite
Installs /dev-loop, /goal, /research (/rs), /websearch (/s), /audit, /sync,
/triage (/tr), /review (/rv), /ship (/sh) across all AI harnesses.
==============================================================================

<!-- mios-src:dfcf7168dd18 from .agents/skills/dev-loop/reference/install.sh:1-6 -->

### review.py - SCOPE Staged Code Review & Invariant...

review.py - SCOPE Staged Code Review & Invariant Verification Engine (/review, /rv).
Audits git diffs for contract drift, security sanitization, invariant violations, and dead code.

<!-- mios-src:ed7dd7d85d09 from .agents/skills/dev-loop/reference/review.py:2-5 -->
