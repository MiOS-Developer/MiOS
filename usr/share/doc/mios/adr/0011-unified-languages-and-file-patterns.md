<!-- AI-hint: Unify the codebase to language-per-domain (Rust/Go for resilient native tooling — build driver, drift-runner, the verb dispatcher that removes the eval surface; Bun/TS for the Portal/configurator; Python stays for the AI plane; TOML SSOT; YAML pipelines; Markdown docs) with bash demoted to thin glue; PLUS a global compiled-template system (one template per file type + `mios new <type>` scaffolder + a conformance drift-check) that formalizes/extends the AI-hint convention so an agent learns MiOS formatting from a few files. Proposes Law 14 ONE-TEMPLATE-PER-TYPE (registry row + enforcement PLANNED, operator-gated). Read before writing a new tool, choosing a language, or adding a file type. -->
<!-- AI-related: automation/98-drift-checks.sh (44 checks), automation/build.sh, usr/lib/mios/mios_toml.py, tools/lib/userenv.sh, usr/lib/mios/agent-pipe/server.py (8961 ln), usr/lib/mios/agent-pipe/mios_dispatch.py, usr/libexec/mios/mios-ai-tag, usr/libexec/mios/mios-theme-render, C:\MiOS\src\mios-launch.cs, C:\mios-bootstrap\cat\, usr/share/mios/mios.toml [laws], usr/share/doc/mios/adr/0007-governance-model-laws-adrs-spec.md, usr/share/doc/mios/adr/0008-mios-cat-unified-entry-and-minification.md -->
---
adr: 0011
title: Unified languages & compiled file-patterns — language-per-domain + one-template-per-type
status: proposed
date: 2026-07-16
deciders: [operator, ai-pair]
tags: [languages, rust, bash, python, typescript, templates, file-patterns, tech-debt, governance]
laws: [7, 8, 9]
ssot_keys: []
related_ws: [WS-LANG, WS-TEMPLATE, WS-DEBT]
supersedes: []
superseded_by: []
---

# ADR-0011: Unified languages & compiled file-patterns — language-per-domain + one-template-per-type

## Status

Proposed — 2026-07-16 (Laws 7, 8, 9). Under review; not yet load-bearing. This ADR
proposes a language-per-domain contract, one compiled-template-per-file-type
system, and a candidate **Law 14 (ONE-TEMPLATE-PER-TYPE)**. Per ADR-0007, adding a
law is *this ADR + a `[laws]` registry row + a drift-check*; the `[laws]` edit and
the enforcement check are **PLANNED under WS-TEMPLATE and flagged for operator
confirmation — this ADR does NOT edit the `[laws]` table.** Ground truth was
re-measured against the live `C:\MiOS` + `C:\mios-bootstrap` trees (see Context);
those corrected numbers, not the older reports', are used throughout.

## Context

The estate is honest and already well-instrumented — 100% AI-hint coverage in
`mios_pipe/`, clean secrets (`mios-hardcode-lint`, Law 7), a working Law-8 projector
(`usr/libexec/mios/mios-theme-render`), and 44 drift-gates — but it carries three
compounding structural debts, and the language inventory itself is a debt.

Re-measured ground truth (the older reports and the operator brief carried drift):

- `usr/lib/mios/agent-pipe/server.py` is **8,961 lines** (not "~26k") — a
  god-module (VRAM scheduler + `_db_*` + auth middleware + agent streaming
  intermixed).
- `automation/98-drift-checks.sh` is **~3,098 lines, 44 `check_*` functions** (not
  "101 checks").
- `automation/build.sh` is **579 lines** — smaller and more tractable to port than
  claimed.
- There are **3× `mios.toml`**: the canonical `usr/share/mios/mios.toml`
  (10,869 ln) plus two diverged ~1.4k-ln roots (`C:\MiOS\mios.toml` and
  `C:\mios-bootstrap\mios.toml`). `VERSION` = **0.3.0** and the SSOT `mios_version`
  = **0.3.0**, but the root `C:\MiOS\mios.toml` says **0.2.4** — the version drift
  is real and current, compounded by **37× hardcoded `v0.2.4`** in script headers.
- There is **zero authored `.rs`/`.go`**, but a **4th compiled language is already
  in-tree and unaccounted: C#/.NET at `C:\MiOS\src\mios-launch.cs`** — and
  `C:\MiOS\src\` is therefore **already occupied**.

The orchestration/validation *logic* (the build driver, the 44-check drift file,
~150 verb scripts, the resolver twin `mios_toml.py` ⇄ `userenv.sh`) is trapped in
fragile bash/batch: `shellcheck` exists only as `# shellcheck source=` comments
(no CI lint job), 23 runtime verbs have no `set -e`, and **9 verbs `eval` on
agent-derived args** (an injection surface). The AI-hint header + `mios-theme-render`
projector *prove* the pattern but enforce it piecemeal (the header check validates
the header, never the body structure). The debt map (TD-1..TD-8) is captured as the
WS-DEBT register.

## Decision

Adopt a **language-per-domain contract** with bash demoted to thin glue, plus a
**global compiled-template system** (one template per file type). Specifically:

1. **Language-per-domain.**
   - **Rust (default native tier)** for resilient tooling/orchestration/validation:
     the build driver, the drift-runner, the verb dispatcher (which removes the
     `eval` surface), the resolver core (collapsing the `mios_toml.py` ⇄
     `userenv.sh` twin into one crate with a `--shell` KEY=VAL emitter + a pyo3
     binding — ending the Law-13 parity drift), the render engine, and the
     installer core. Static musl binary, negligible deps, network-transportable,
     cross-compiles to a Windows `.exe`. **Go is a documented escape hatch, not
     adopted** — reserved only for a future high-volume/low-stakes need (OPEN
     QUESTION).
   - **Bash stays — thin only**, for the ~66 `automation/NN-*.sh` steps (they *are*
     the `dnf`/`bootc`/`rpm-ostree`/`semanage` boundary — reimplementing buys
     fragility), systemd `ExecStart` shims, and sub-20-line firstboot/verb backends,
     all held to the `bash` template (`set -euo pipefail` + `main()`).
   - **Python stays** for the AI/agentic plane (Law 6 OpenAI /v1-only; the ML
     ecosystem is Python-native). The debt there is the monolith, not the language.
   - **Bun/TypeScript** confined to the web Portal/configurator (never for
     privileged `/usr` system tools — `bun build --compile` drags a ~90 MB runtime
     and the npm supply-chain surface).
   - **TOML** SSOT · **YAML** pipelines/quadlets (generated-only) · **Markdown**
     docs (template-compiled). **Batch eliminated (100%); C# `mios-launch.cs`
     folded into the Rust installer core.**

2. **One `miosd` static binary** — a cargo workspace (subcommands
   `build|drift|verb|resolve|render|cat|scaffold|fmt`), baked once in an early
   cached Containerfile stage and `COPY`'d to `/usr/libexec/mios/miosd`, invoked by
   **thin RUNs** — so the immutable-image contract holds. **Law 8 is strengthened,
   not bypassed:** `miosd render`/`drift`/`fmt` are all the same regenerate-and-diff
   gate contract `mios-theme-render check` already uses, deterministic and offline.

3. **A compiled file-pattern system — one template per file type (~15 types).**
   A template = the shared AI-hint header block (produced by the *same* engine,
   `usr/libexec/mios/mios-ai-tag`, so the header stays single-sourced) + a small
   per-type body skeleton whose *structure* is also validated (closing the current
   gap where only the header is checked). Templates live under
   `usr/share/mios/templates/<type>.tmpl`, declared in SSOT (`[templates.<type>]`),
   scaffolded by `mios new <type>` (land first as Python `usr/libexec/mios/mios-new`
   reusing `mios-ai-tag`, then absorb into `miosd scaffold`), and enforced by a new
   `check_template_conformance` drift-check + a golden round-trip compiler
   (`tools/compile-templates.py`) — mirroring the existing `check_hint_coverage →
   mios-ai-hint-coverage` pattern (degrades open on missing python3, image-free PR
   gate, ratchets to zero). `generated=true` types (quadlets) refuse to scaffold an
   editable file and instead scaffold the *generator + its `mios.toml` section*, so
   Law 8 stays authoritative — no double drift-gate. **Result:** the whole
   deliverable an agent reads to learn MiOS formatting is `templates/*.tmpl` + the
   `[templates]`/`[ai_tag]`/`[laws]` tables.

4. **Candidate Law 14 — ONE-TEMPLATE-PER-TYPE (PROPOSED, operator-gated).** Every
   authored file type has exactly one compiled, golden-tested template and is
   born from and validated against it. Per ADR-0007 this requires a `[laws]`
   registry row (id 14) + `check_template_conformance` as its `enforced_by`. Both
   the registry edit and the enforcement are **PLANNED under WS-TEMPLATE and left
   for operator confirmation — this ADR does not touch the `[laws]` table.**

**Sequencing (biggest-resilience-win-first, lowest-risk-first).** Phase −1 stops
the bleeding with no new toolchain: a `shellcheck` CI job + `set -euo pipefail` on
the 23 unguarded verbs + an audit of the 9 `eval` sites (TD-1); collapse version to
one projected token and make the two root `mios.toml` generated projections or
delete them (TD-2); route all TOML reads through one resolver shim (TD-3). Then
stand up the Rust workspace and land the template system, and port the first fragile
bash tool — the **drift-runner** (highest win, lowest coupling; several checks are
already Python-in-bash) or the **verb dispatcher** — running old+new side-by-side and
diffing to identical before deleting the bash.

> [!NOTE]
> **Implementation Note (task_1078):** The native cargo workspace was stood up at `tools/native/` with `mios-version-check` as the first tool. In the absence of a host Rust toolchain, a `TODO(agy): cargo build` is recorded.

## Rationale

- **The migration surface is logic-dense orchestration/validation, not high-volume
  glue** — Rust's sweet spot. The bulk straight-line `dnf`/`bootc`/`systemctl` steps
  stay bash.
- **One native language beats two for the "learn from a few files" goal** (Law 8's
  legibility intent). A second native tier (Go) is net-negative for that goal, so Go
  is rejected by default.
- **The deepest structural win favors Rust specifically** — collapsing the
  `mios_toml.py` ⇄ `userenv.sh` twin (TD-3) into one crate with `--shell` + pyo3
  faces ends the Law-13 parity drift with zero-GC determinism.
- **Law 8 (SSOT-PROJECTION) mechanically enforced.** The template system makes every
  authored file *born from* and *validated against* a compiled template — the same
  regenerate-and-diff discipline the projector already proves.
- **Law 7 (NO-HARDCODE) / Law 9 (ONE-CANONICAL-NAME).** The scaffolder fills
  canonical fields (next ADR number, next `automation/NN` ordinal, canonical
  ports/endpoints) from SSOT and registers the canonical name via
  `tools/generate-names-registry.py`, so a new verb/unit can't collide or hardcode.
- **Meta-argument.** That the operator brief, three reports, and the codebase docs
  all disagreed on `server.py`'s size *is itself* debt (TD-8) and the strongest case
  for a metric-re-derivation gate.

**Code-gen precedents:** cookiecutter (Jinja2 + a JSON context — closest cultural
fit, MiOS is Python-first and already context-drives generators from `mios.toml`),
copier (re-render already-scaffolded files as the template evolves — exactly MiOS's
projection philosophy), plop/hygen (per-action micro-generators — the `mios new
<type>` action model), cargo-generate (post-scaffold hooks — register name, re-run
names-registry, stamp AI-hint).

## Alternatives considered

- **Go as the native default (or a second native tier).** Rejected — one toolchain
  serves the "few files" goal better; Go's GC and weaker Python-FFI story make the
  resolver-twin collapse clumsier. Kept as a documented escape hatch only.
- **Bun/TypeScript for system tools.** Rejected for privileged `/usr` — ~90 MB
  runtime + the npm supply-chain surface; fine for the web Portal only.
- **Rewrite the 66 `automation/NN-*.sh` OS-touching steps in Rust.** Rejected — they
  are the correct use of bash (the `dnf`/`bootc`/SELinux boundary); the logic
  (order/gating/DAG) moves to the driver, the steps stay thin.
- **Keep the AI plane's language open / rewrite in Rust.** Rejected — Python is the
  right language (Law 6, ML ecosystem); the debt is the monolith, fixed by finishing
  the `mios_pipe/` decomposition.
- **Templates as inert text files.** Rejected — a template that can't produce a
  conformant file must fail the build; "compiled" means golden round-trip + a
  conformance drift-check, so templates are proven artifacts, not rot.

## Consequences

Positive:
- More resilient than bash for the correctness-critical logic; one cross-compiled
  binary serves Linux and Windows (retiring the Batch crash-class and the C#
  launcher).
- Agents learn all MiOS formatting from `templates/` + one example per type; the
  AI-hint convention becomes an enforced, body-aware contract.
- Ends the Law-13 resolver-twin parity drift; removes the `eval`-on-agent-args
  surface; the FATAL/WARN swallowing class disappears with the build driver.

Costs / honest status:
- Adds a cached Rust build stage and a steeper toolchain curve (bounded — a small
  crate set, built once, cached).
- **PROPOSED, nothing landed:** the language contract, the `miosd` binary, the
  template system, Law 14, and every migration step are all planned (WS-LANG,
  WS-TEMPLATE, WS-DEBT). The AI plane and the 66 OS-touching steps are deliberately
  unchanged.
- The `[laws]` edit for Law 14 and its enforcement check await operator confirmation.

## Implementation

- **WS-DEBT** — the technical-debt register (TD-1..TD-8). Phase −1: a `shellcheck`
  CI job + `set -euo pipefail` on the 23 unguarded verbs + audit the 9 `eval` sites
  (task_247); collapse the 3× `mios.toml` + the 0.2.4 root + the 37× hardcoded headers
  to one projected version token (task_246); split `mios_dispatch.py` / finish the
  `server.py` (8,961 ln) decomposition (task_251).
- **WS-LANG** — the cargo workspace (location an OPEN QUESTION: `C:\MiOS\src\` is
  occupied by `mios-launch.cs` + `autounattend/`, so the workspace goes elsewhere,
  e.g. `C:\MiOS\tools\native\` or `src\mios-rs\`, not clobbering `src/`); the early
  Containerfile Rust stage; the first bash→Rust port — the drift-runner or verb
  dispatcher — old+new side-by-side, diffed to identical (task_250).
- **WS-TEMPLATE** — `usr/share/mios/templates/*.tmpl` (~15) + `[templates]` schema;
  Python `usr/libexec/mios/mios-new` (`mios new <type>`, reusing `mios-ai-tag`);
  `tools/compile-templates.py` golden tests; `check_template_conformance`
  (`automation/98-drift-checks.sh`, soft→hard ratchet); the candidate Law-14 `[laws]`
  row + enforcement, operator-gated (task_249).
- Reference impls kept: `usr/libexec/mios/mios-theme-render` (the clean Law-8
  projector the render/drift crates copy); `usr/libexec/mios/mios-ai-tag` (the
  header machinery the templates reuse).

## References

- ADR-0007 (Governance model) — laws as fitness functions; adding Law 14 is a `[laws]`
  row + a drift-check: `0007-governance-model-laws-adrs-spec.md`.
- ADR-0008 (MiOS-Cat unified entry point) — the installer-core unification the Rust
  `miosd cat` completes (retiring `.bat` + `mios-launch.cs`):
  `0008-mios-cat-unified-entry-and-minification.md`.
- Re-measured ground truth: `usr/lib/mios/agent-pipe/server.py` (8,961 ln),
  `automation/98-drift-checks.sh` (44 `check_*`), `automation/build.sh` (579 ln),
  `usr/share/mios/mios.toml` (10,869 ln) vs `C:\MiOS\mios.toml`/`C:\mios-bootstrap\mios.toml`
  (~1.4k ln, root=0.2.4), `C:\MiOS\src\mios-launch.cs` (C#).
- Pattern seeds: `usr/libexec/mios/mios-ai-tag` (header machinery),
  `usr/libexec/mios/mios-theme-render` (Law-8 projector),
  `usr/lib/mios/mios_toml.py` + `tools/lib/userenv.sh` (the resolver twin to collapse).
- Code-gen precedents: cookiecutter, copier, plop/hygen, cargo-generate, yeoman.
- MiOS Laws 7/8/9 (+ proposed 14): `usr/share/mios/mios.toml [laws]`, enforced by
  `automation/98-drift-checks.sh` + `automation/99-postcheck.sh`.
