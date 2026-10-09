<!-- AI-hint: ADR-0030 caps MiOS at 500 tracked source files (dependencies excluded): shrink-only per-domain budgets in [legibility.file_budget], projections become build outputs, and code folds into few MiOS-MODULES. -->
<!-- AI-related: /usr/share/mios/mios.toml [legibility], [legibility.file_budget], [drift.generated_ceilings], tools/drift-checks.py, tests/drift-gate-negatives.sh, /usr/share/doc/mios/adr/0010-ssot-as-system-dotfiles.md, /usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md -->
---
adr: 0030
title: A 500-file source budget -- shrink-only domain budgets, projections as build outputs, folds into MiOS-MODULES
status: proposed
date: 2026-10-09
deciders: [operator, ai-pair]
tags: [legibility, file-budget, ratchet, projections, modules, consolidation]
laws: [8, 9, 14, 16]
ssot_keys: [legibility, legibility.file_budget, drift.generated_ceilings]
related_ws: []
supersedes: []
superseded_by: []
---

# ADR-0030: A 500-file source budget -- shrink-only domain budgets, projections as build outputs, folds into MiOS-MODULES

## Status

proposed — 2026-10-09.

The operator's directive: "must consolidate the amount of disparate files to a more manageable amount (thinking about 500 files maximum—not including dependency related files..) theres many files that could and should be combined".

An agent never flips this record to accepted.

## Context

On 2026-10-09 the tree tracked 3,662 files. 101 are dependencies: 99 vendored wheels and archives under `usr/share/mios/vendored/`, plus two `Cargo.lock`. The other 3,561 are MiOS's own.

`[legibility].max_tracked_files` was meant to be a shrink-only ratchet. It sat in `[drift.generated_ceilings]`, though, so `ratchet-direction` skipped it, and it was "re-baselined" upward merge after merge, from 3,050 to 3,662. A ceiling that rises whenever it is reached does not bound anything.

The 3,561 source files fall into these domains (first glob match wins):

| Domain | Files | Target | How it folds |
|---|---:|---:|---|
| projections | 524 | 5 | Rendered by the build from SSOT, not tracked: man pages, units, Quadlets, drop-ins, `tmpfiles`/`sysusers`/`sysctl`/`modprobe`, desktop entries, generated indexes. Human-facing views (`ROADMAP.md`, `TASKS.md`, `ADR.md`) stay. |
| docs | 659 | 40 | One manual volume per domain; ADRs become one decision log; research, findings and handoffs fold into their volume or the ledger. |
| modules | 549 | 45 | `usr/libexec` + `usr/bin` verbs become a few domain MiOS-MODULES, multicall by `argv[0]` (ruling Q19); the verb symlinks are rendered at build. |
| data | 435 | 60 | `usr/share/mios` and `usr/lib` data becomes SSOT tables or one bundle per domain. |
| agent-pipe | 414 | 40 | The 412-module package (88.6k lines) becomes domain modules. |
| tests | 281 | 40 | One suite per domain; fixtures become data bundles. |
| rust | 210 | 60 | One workspace; crates are the MiOS-MODULES. |
| automation | 160 | 15 | Phases are data in `[build.phases]`, run by the Rust build driver. |
| etc | 123 | 25 | `profile.d` (48) becomes one file per shell; `skel` is rendered by the dotfiles registry (ADR-0010). |
| tools | 96 | 30 | `tools/*.py` move into `mios-gen` / `mios-gate`. |
| misc | 61 | 20 | Root dotfiles and `var` seeds are rendered or folded. |
| harness | 49 | 20 | Per-harness shims (`.claude`, `commands/*`, skills) are rendered from one source at dev setup. |
| **total** | **3,561** | **400** | Headroom of 100 under the 500 cap |

## Decision

1. **The file ceiling counts source files.** `[legibility.file_budget].dependency_globs` removes dependencies from `max_tracked_files`, by SSOT glob, never by a list in code. Dependencies still count toward `max_tracked_mb`, which Law 12 sizes. `target_source_files = 500` is the operator's cap.
2. **The ceiling is really shrink-only now.**
   - `legibility.max_tracked_files` leaves `[drift.generated_ceilings]`, so `ratchet-direction` refuses any increase over HEAD.
   - Each domain gets `[legibility.file_budget.domains.<name>]` with `globs`, `max_files` (shrink-only, seeded at today's count) and `target_files`. Each source file counts in the first domain of `order` whose glob matches it. A file that matches no domain fails the gate, and so do targets that sum past the cap.
3. **No net new files.** From now on, a commit that adds a source file must remove at least one in the same commit. Every fold lane lowers its domain's `max_files` to the new count in the commit that folds.
4. **Projections are build outputs.**
   - A file a generator renders from SSOT is produced by the build (the image and the CI job) and is not tracked. The drift gate stops diffing committed copies. Instead it renders twice and requires byte-identical output, then checks that every projection the registry declares was rendered.
   - The runtime image is unchanged: units, man pages and Quadlets still exist at their FHS paths, rendered in the build.
   - Only human-facing views stay tracked.
5. **Every fold proves it changed nothing at runtime.** A fold lane records the rendered root-overlay manifest (path + sha256 of every file the build would install) before and after. The two manifests must be identical except for differences the lane names. Behaviour tests must pass unchanged.
6. **Size versus count.** This budget supersedes any per-file line ceiling proposed for the same code (the agent-pipe 800-line module ceiling): a large module with an AI-hint and a section index is preferred to many small files. Each folded file keeps a one-line AI-hint and a section table of contents.

## Milestones

Each milestone lowers `max_tracked_files` and the affected domains.

| Phase | Work | Source files after |
|---|---|---:|
| F0 | This gate + ADR + epic | 3,562 |
| F1 | Projections become build outputs | ~3,040 |
| F2 | Cheap folds: `etc/profile.d`, `tmpfiles`/`sysctl`/`modprobe`/`kargs`, harness shims, misc | ~2,750 |
| F3 | Tests: one suite per domain | ~2,500 |
| F4 | Docs: manual volumes, one ADR log | ~1,900 |
| F5 | agent-pipe: domain modules | ~1,550 |
| F6 | MiOS-MODULES: libexec verbs into multicall modules | ~1,050 |
| F7 | One Rust workspace; automation phases as data | ~750 |
| F8 | Data and tools into SSOT tables and Rust | ≤ 500 |

## Consequences

- **Large folds are hard to review.** Each fold lane moves code without changing it: one commit moves, a separate commit edits. Rename-aware history is lost, so the move commit's message lists every source → destination pair.
- **Tooling that reads one file per record must learn sections.** That covers AI-hint and manual indexing, `mios-gen`'s ADR index and the manual corpus ledger. Their phases change them first.
- **Lanes already in flight must stay inside the budget.** They put new code in existing files rather than new ones.

## Sources

The operator's directive in session, 2026-10-09; the measurements in this record, taken from `git ls-files` at `ef7a0c69`.
