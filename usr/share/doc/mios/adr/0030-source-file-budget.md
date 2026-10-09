<!-- AI-hint: ADR-0030 caps EACH MiOS repo (mios.git and mios-bootstrap) at 500 tracked files TOTAL (dependencies included): shrink-only per-domain budgets in [legibility.file_budget], projections become build outputs, and code folds into few MiOS-MODULES. -->
<!-- AI-related: /usr/share/mios/mios.toml [legibility], [legibility.file_budget], [drift.generated_ceilings], tools/drift-checks.py, tests/drift-gate-negatives.sh, /usr/share/doc/mios/adr/0010-ssot-as-system-dotfiles.md, /usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md -->
---
adr: 0030
title: 500 tracked files per MiOS repo -- shrink-only domain budgets, projections as build outputs, folds into MiOS-MODULES
status: proposed
date: 2026-10-09
deciders: [operator, ai-pair]
tags: [legibility, file-budget, ratchet, projections, modules, consolidation]
laws: [8, 9, 12, 14, 16]
ssot_keys: [legibility, legibility.file_budget, drift.generated_ceilings]
related_ws: []
supersedes: []
superseded_by: []
---

# ADR-0030: 500 tracked files per MiOS repo -- shrink-only domain budgets, projections as build outputs, folds into MiOS-MODULES

## Status

proposed — 2026-10-09.

The operator's directive came in two steps. First: "must consolidate the amount of disparate files to a more manageable amount (thinking about 500 files maximum…) theres many files that could and should be combined". Then: "500 disparate files—TOTAL/OVERALL".

So the cap counts every tracked file, dependencies included. A third step widened it: "500 files is the target for the entire MiOS Platform!!! MiOS Main repo (as well as the sister repo; bootstrap) is set to be a total of 500 files". A fourth step settled the scope: "narrowing the overall File count GLOBALLY to 500 total files EACH per MiOS repo(s) -- we need no loss of functionalities, features, systems". The cap is 500 per repo: mios.git and mios-bootstrap each, folded losslessly, with the Rust static-binary port as the primary goal and the hosted `mios.html` -> Rust -> `mios.toml` flow kept intact.

An agent never flips this record to accepted.

## Context

On 2026-10-09 the tree tracked 3,662 files. `[legibility].max_tracked_files` was meant to be a shrink-only ratchet, but it sat in `[drift.generated_ceilings]`. That let `ratchet-direction` skip it, so it was re-baselined upward merge after merge, from 3,050 to 3,662. A ceiling that rises whenever it is reached does not bound anything.

The tree is also full of copies:
- **16 tracked files are empty:** `.keep` markers, plus an empty `.cursorrules` and `.clinerules`.
- **9 harness commands exist four times each,** byte for byte, under `.agents/skills/dev-loop/commands/claude`, `.agents/workflows`, `.claude/commands` and `commands/claude`.
- **Other identical copies:** five getty units, three keybinding files, three code-server settings and three drop-ins.
- **Two Windows executables are the same bytes.**

This record's own commit deletes the 9 unreferenced `commands/claude` copies, which leaves 3,654 files.

The 3,654 files fall into these domains (first glob match wins):

| Domain | Files | Target | How it folds |
|---|---:|---:|---|
| docs | 651 | 40 | One manual volume per domain; ADRs become one decision log; research, findings and handoffs fold into their volume or the ledger. |
| modules | 549 | 45 | `usr/libexec` + `usr/bin` verbs become a few domain MiOS-MODULES, multicall by `argv[0]` (ruling Q19); the verb symlinks are rendered at build. |
| projections | 524 | 5 | Rendered by the build from SSOT, not tracked: man pages, units, Quadlets, drop-ins, `tmpfiles`/`sysusers`/`sysctl`/`modprobe`, desktop entries, generated indexes. Human-facing views (`ROADMAP.md`, `TASKS.md`, `ADR.md`) stay. |
| data | 435 | 60 | `usr/share/mios` and `usr/lib` data becomes SSOT tables or one bundle per domain. |
| agent_pipe | 414 | 40 | The 412-module package (88.6k lines) becomes domain modules. |
| tests | 281 | 40 | One suite per domain; fixtures become data bundles. |
| rust | 210 | 60 | One workspace; crates are the MiOS-MODULES. |
| automation | 160 | 15 | Phases are data in `[build.phases]`, run by the Rust build driver. |
| etc | 123 | 25 | `profile.d` (48) becomes one file per shell; `skel` is rendered by the dotfiles registry (ADR-0010). |
| dependency | 101 | 5 | The 99 vendored wheels and archives become one bundle per kind. Law 12 still bakes them, just not as 99 files. One `Cargo.lock` once the workspaces are one. |
| tools | 96 | 30 | `tools/*.py` move into `mios-gen` / `mios-gate`. |
| misc | 61 | 20 | Root dotfiles and `var` seeds are rendered or folded. |
| harness | 49 | 20 | Per-harness shims are rendered from one source at dev setup. |
| **mios.git total** | **3,654** | **405** | 95 files of headroom under its 500 cap |

mios-bootstrap is budgeted separately: 201 files today against its own 500 cap (`[legibility].max_bootstrap_files`, shrink-only; `target_bootstrap_files`). It is already under the cap and still folds where that is lossless: `field/` (137, the MiOS-Field SystemRescue assets) into SSOT-rendered bundles (task_3672), and its 24 root files into one installer entry per platform (task_3673).

## Decision

1. **The cap is 500 tracked files per repo, dependencies included.** `max_tracked_files` counts mios.git's whole `git ls-files` listing (ratchet-direction also compares it with main's tree), partitioned into the domains below, with `[legibility.file_budget].target_tracked_files = 500`. `[legibility].max_bootstrap_files` counts mios-bootstrap's listing, with `target_bootstrap_files = 500`. The gate reads the sister repo from `MIOS_BOOTSTRAP_ROOT` (default `../mios-bootstrap`); when it is absent its budget is unmeasured, which is a violation, never a smaller number that passes.
2. **The ceiling is really shrink-only now.**
   - `legibility.max_tracked_files` leaves `[drift.generated_ceilings]`, so `ratchet-direction` refuses any increase over HEAD.
   - Each domain has `[legibility.file_budget.domains.<name>]` with `globs`, a `max_files` ceiling seeded at today's count (shrink-only) and a `target_files`.
   - Every tracked file counts in the first domain in `order` whose glob matches it. The gate fails if a file matches no domain, or if the targets sum past the cap.
3. **No net new files.** A commit that adds a file must remove at least one in the same commit. Every fold lane lowers its domain's `max_files`, and `max_tracked_files`, to the new counts in the commit that folds.
4. **Projections are build outputs.** A file a generator renders from SSOT is produced by the build (the image and the CI job) and is not tracked.
   - The drift gate stops diffing committed copies. Instead it renders twice and requires byte-identical output, then checks that every projection the registry declares was rendered.
   - The runtime image is unchanged: units, man pages and Quadlets still exist at their FHS paths.
   - Only the human-facing views stay tracked.
5. **Every fold proves it changed nothing at runtime.** A fold lane records the rendered root-overlay manifest, path + sha256 of every file the build would install, before and after. The two must be identical except for differences the lane names, and behaviour tests pass unchanged.
6. **Size versus count.** This budget supersedes any per-file line ceiling proposed for the same code, such as the 800-line agent-pipe module ceiling. A large module with an AI-hint and a section index is preferred to many small files, so each folded file keeps a one-line AI-hint and a table of contents.

## Milestones

Each milestone lowers `max_tracked_files` and the affected domains.

| Phase | Work | Tracked files after |
|---|---|---:|
| F0 | This gate, this ADR and the epic; delete the 9 duplicate command copies | 3,654 |
| F1 | Projections become build outputs; exact duplicates and empty markers go | ~3,080 |
| F2 | Cheap folds: `etc/profile.d`, `tmpfiles`/`sysctl`/`modprobe`/`kargs`, harness shims, the vendored bundle, misc | ~2,700 |
| F3 | Tests: one suite per domain | ~2,450 |
| F4 | Docs: manual volumes, one ADR log | ~1,850 |
| F5 | agent-pipe: domain modules | ~1,480 |
| F6 | MiOS-MODULES: libexec verbs into multicall modules | ~980 |
| F7 | One Rust workspace; automation phases as data | ~700 |
| F8 | Data and tools into SSOT tables and Rust | ≤ 500 |

## Consequences

- **Large folds are hard to review.** Each fold lane moves code without changing it: one commit moves, a separate commit edits. Rename-aware history is lost, so the move commit's message lists every source → destination pair.
- **Tooling that reads one file per record must learn sections.** That covers AI-hint and manual indexing, `mios-gen`'s ADR index and the manual corpus ledger. Their phases change them first.
- **Lanes already in flight stay inside the budget.** They put new code in existing files rather than new ones.
- **Folding the vendored wheels changes the build's install step, not what is baked.** The same wheels install from one archive (Law 12).

## Sources

The operator's directives in session, 2026-10-09; the measurements in this record, from `git ls-files` at `ef7a0c69` plus this commit's deletions.
