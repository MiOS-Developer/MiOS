<!-- AI-hint: The governance model — laws are enforced invariants (fitness functions), ADRs are decisions, and a generated MiOS Spec renders both; read before "converting" laws to anything. -->
<!-- AI-related: usr/share/mios/mios.toml [laws], usr/share/mios/mios.toml [conventions], automation/98-drift-checks.sh, automation/99-postcheck.sh, usr/share/doc/mios/adr/README.md, CLAUDE.md -->
---
adr: 0007
title: Governance model — laws as fitness functions, ADRs as decisions, a generated MiOS Spec (OpenAI Model-Spec pattern)
status: accepted
date: 2026-07-12
deciders: [operator, ai-pair]
tags: [governance, laws, adr, spec, openai, policy-as-code]
laws: [7, 8]
ssot_keys: [laws, conventions]
related_ws: [WS-DOCS]
supersedes: []
superseded_by: []
---

# ADR-0007: Governance model — laws as fitness functions, ADRs as decisions, a generated MiOS Spec

## Status
Accepted — 2026-07-12.

## Context
MiOS (an immutable `bootc`/OCI Fedora image that is also a local, OpenAI-compatible agentic AI OS; SSOT = `usr/share/mios/mios.toml`) accreted several overlapping governance artifacts:

- the **13 architectural LAWS** — registered in `mios.toml [laws]` (the id/slug/`applies_to`/`enforced_by` numbering SSOT), described in `CLAUDE.md`, and enforced by `automation/98-drift-checks.sh` (offline), `automation/99-postcheck.sh` (at bake), and `bootc container lint`;
- soft **CONVENTIONS** (latest-packages, OpenAI-API-only, every-artifact-tracked, persistence-sanitization);
- an **honesty rule** ("DONE = active *and* live-fired");
- and now a set of **ADRs** (`usr/share/doc/mios/adr/`, ADR-0001..0006).

The operator asked whether the LAWS (and the rest) should be "converted to ADRs, or whatever is the most lightweight fashion." The trap: an ADR is an immutable point-in-time **decision**; a law is a continuously-**enforced invariant**. Collapsing one into the other loses what makes each useful — an invariant would look like frozen history, and its machine-enforceable registry would disappear.

## Decision
Keep **three distinct, cross-linked governance layers**; do **not** convert laws to ADRs.

1. **LAWS = fitness functions (policy-as-code).** The 13 laws remain enforced INVARIANTS. Numbering SSOT: `mios.toml [laws]`. Enforcement: `98-drift-checks.sh` + `99-postcheck.sh` + `bootc container lint`. A law is a rule the build must never violate — not a decision. Laws evolve by editing the registry and its enforcement, never by "superseding an ADR."
2. **ADRs = decisions (the why).** ADRs record point-in-time architectural decisions — including the decision to ESTABLISH or AMEND a law. Immutable, numbered, superseded-not-rewritten. An ADR's `laws[]` frontmatter cross-links the decision to the invariant it created or touches.
3. **The MiOS Spec = the human/agent-readable rules doc, patterned on the OpenAI Model Spec.** A GENERATED reference (Law 8 SSOT-PROJECTION) under `usr/share/doc/mios/spec/`, rendered from `mios.toml [laws]` + a new `[conventions]` registry, using the Model Spec's hierarchy — hard **RULES** (the 13 laws) over soft **DEFAULTS** (conventions). Each entry carries: the statement, `applies_to`, `enforced_by` (the drift-check), and rationale (→ the establishing ADR). The drift-checks are the "**evals**" that enforce the Spec.

Net stack: **Spec (rules) ← ADRs (decisions) ← drift-checks (evals)** — all SSOT-generated and cross-referenced. That is the lightweight, OpenAI-aligned governance model.

## Rationale
- **Laws-as-fitness-functions is the established policy-as-code pattern** (architectural fitness functions à la *Building Evolutionary Architectures*; OPA/conftest gatekeeping). MiOS already implements it as the drift-gate; converting to ADRs would delete the machine-enforceable registry and freeze living rules into history.
- **ADRs already carry `laws[]`.** The cross-link exists; the right move is to formalize the relationship, not collapse the categories.
- **The OpenAI Model Spec is the industry reference** for a *lightweight, hierarchical, human-readable* governing-rules document with a clear precedence order (platform > developer > user → here: laws > conventions). Rendering the MiOS Spec from the SSOT keeps it drift-free (Law 8) and gives an arriving agent one canonical rules doc instead of prose scattered across `CLAUDE.md`, `AGENTS.md`, and the registry.

## Alternatives considered
- **Convert every law into an ADR.** Rejected — category error (invariant ≠ decision); removes the enforceable registry; makes evolving a rule a superseding-ADR ceremony instead of a one-line registry edit + drift-check.
- **Fold laws + conventions + ADRs into one giant governance document.** Rejected — mixes enforced invariants, soft defaults, and historical decisions in one file: the exact sprawl WS-DOCS is removing.
- **Leave the laws only as `CLAUDE.md` prose.** Rejected — not machine-readable, drifts from the registry, no agent-navigable spec.

## Consequences
- **Zero churn to the enforcement path** — the drift-checks are unchanged; laws keep working exactly as they do.
- Adds one generated artifact (the MiOS Spec), one SSOT registry (`[conventions]`), and one generator + drift-check — tracked as **WS-DOCS / T-255** (the roadmap item that preceded it was never recorded on its own).
- The lifecycle is now explicit: **establishing a new law** = an ADR (the decision) + a `[laws]` registry row + a drift-check (the enforcement) → it appears in the generated Spec automatically. **Amending a law** = a new ADR + a registry/enforcement edit; the old ADR is superseded, but the law itself is edited in place (it is an invariant, not a historical record).

## Implementation
- **Exists:** `usr/share/mios/mios.toml [laws]` (registry); `automation/98-drift-checks.sh` + `99-postcheck.sh` + `bootc container lint` (enforcement); `usr/share/doc/mios/adr/README.md` record table + `laws[]` cross-links (ADR↔law).
- **PLANNED (WS-DOCS / T-255):** `usr/share/mios/mios.toml [conventions]` (soft-defaults SSOT); `tools/generate-mios-spec.py` → `usr/share/doc/mios/spec/README.md` (Model-Spec-style, generated); a regenerate-and-diff drift-check in `98-drift-checks.sh`. `CLAUDE.md`/`AGENTS.md` then link to the generated Spec instead of re-stating the laws inline.

## References
- OpenAI **Model Spec** — a hierarchical objectives/rules/defaults governing-rules document; the pattern for the MiOS Spec.
- Architectural **fitness functions** / policy-as-code (OPA/conftest) — the pattern for laws-as-drift-checks.
- [MADR](https://adr.github.io/madr/) (ADR format); MiOS ADR-0001..0006; `mios.toml [laws]`; `automation/98-drift-checks.sh`.
