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

7. **Every dropped file is kept, in `.archive/`, in git's own format (operator directive 2026-10-09: "retain all dropped files as archive in '.archive' folder as a legacy artifact(s)"; "git overlays to root / .git is a dotfile/folder / repo is root -- THAT is the format").**
   - **One tree.** MiOS's repo root, its `.git`, the FHS root, the OCI image, the bootc bootable image and the AI images are the same root tree. Git overlays to root, and `.git` is just a dotfolder in it. The archive follows the same form.
   - **`.archive/` is a bare git repository beside `.git`:** sha256 object format, compressed packfiles, one branch `legacy`. Each commit records dropped files at their real root-relative paths, at their last content and mode, with the dropping commit and source repo in the message. Restoring is an overlay onto root: `git --git-dir=/.archive --work-tree=/ checkout legacy -- <path>`.
   - **It is maintained by `mios-gen archive`, never by hand.** The tool computes the dropped set from first-parent history since `[legibility.archive].since`, appends one commit per update, and repacks to a single pack. Only a handful of tracked files result: `HEAD`, `config`, `refs/heads/legacy` and `objects/pack/pack-*.{pack,idx,rev}`. These are marked binary in `.gitattributes`, so no line-ending conversion can touch them.
   - **`mios-gen archive --check` fails** when:
     - a dropped file is missing from `legacy`'s tree;
     - an archived file's content differs from its last version;
     - the store fails `git fsck --strict`;
     - history is shallow.
     It compares content, not pack bytes, because packs are not byte-reproducible across git versions.
   - **The `archive` domain holds those few files.** The lane that creates the store pays for them by dropping, and archiving, at least as many files in the same commit.
   - **mios-bootstrap gets its own `.archive/`** in the same form when it first drops a file. Each repo is its own root.
8. **There are no tracked bundle tarballs; the tree is the format (operator: "organize and compress these bundles in an OpenAI/Open Source Ai model's format(s); to OCI standards ... that is mios-micro"; "MiOS's root/.git/FHS/OCI/Bootc Bootable images/Ai images are all aligned in the same root/repo tree").**
   - **Content-addressed and compressed:** git stores already are, like OCI layouts, with sha256 digests in `.archive`. OCI and AI-model packaging happens at publish time, from the tree, not as tarballs committed into it:
     - the bootc/OCI image is built from the root tree;
     - **MiOS AI**, mios-micro, the model derived from MiOS itself, packages its artifacts as CNCF ModelPack v0.0.7 plus a KitOps Kitfile 1.0.0 (ADR-0027 Tier 2).
   - **Lineage: MiOS → MiOS AI.** The `legacy` history in `.archive/` is a corpus source for MiOS AI. When mios-micro packages it, it does so with the `mios-ai` descriptor family and an OpenAI chat fine-tuning JSONL `dataset` layer: path and last commit in the user turn, content in the assistant turn. The index rows are OpenAI-shaped `{"object": ...}` objects, per the ruling that all schemas are OpenAI formats.
   - **The fold-map bundles follow the same rule.** The vendored dependencies, field assets and theme assets in the fold map are kept as root-tree content (fewer, larger files, or git-native stores like `.archive`), not as tarballs. Their OCI forms are produced when the image or artifact is published.

### Operating the legacy archive

`[legibility.archive]` in `mios.toml` holds every value: the path, the branch, the object format, the `since` commit, the author and the `exclude` globs.

- **Dropping files.** In the commit that drops them, after `git rm`, run `mios-gen archive`. It reads the index, so the drop and the archive land together. Stage `.archive/` by explicit path: the old pack's removal and the new pack. A store that is already current is left alone.
- **What a rebuild writes.** One commit, rebuilt from history each time rather than appended. It has a fixed author and a date from `SOURCE_DATE_EPOCH` or the `since` commit, so its id is reproducible, and its message names the newest first-parent commit that held each path. The pack is written without a bitmap or `.rev`, so the store is five files.
- **Checking.** `mios-gen archive --check` is drift check `legacy-archive`. It needs full history (`fetch-depth: 0`); both publishers' drift-gate checkouts have it. It is skipped inside the image bake, because gates are out of the bake.
- **Restoring a file** is an overlay onto root: `git --git-dir=/.archive --work-tree=/ restore --source=legacy -- <path>`. It writes no index into the store. `checkout legacy -- <path>` works too, but it leaves `/.archive/index`, which the check rejects as a stray file, so delete it afterwards. Bytes are exact where `core.autocrlf` is unset, as on every MiOS host.
- **A merge conflict in `.archive/*`:** take either side (`git checkout --ours -- .archive` or `--theirs`), run `mios-gen archive`, and stage the result. The store is derived from history, so neither side's bytes matter.

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

## Fold map (survey of 2026-10-09)

The operator asked: "survey where and what files can be folded lossless into itself … no loss of functionalities, features, systems … porting to rust static binaries as the primary goal … keeping the flow from hosted html -> rust -> toml".

Six read-only surveys mapped every tracked file, measured with `git ls-files` at `d7e84986`–`ef2cdeb4`. Each fold below states its mechanism and its proof that nothing is lost.

| Area | Now | After | Main mechanism |
|---|---:|---:|---|
| projections + etc + usr data + dependency | 1,183 | ~30 | **Render at build:** man pages, Quadlets, units, sysconf, indexes. Hand-written bodies become verbatim bundles in `usr/lib/mios/mios.d/` fragments, rendered back to the same FHS paths. Tunable values become `mios.toml` tables edited through `mios.html` → Rust `toml_edit` → `mios.toml`. The 99 vendored files become ≤ 95 MB tar bundles that the build unpacks to the same paths (Law 12). |
| libexec + usr/bin + tools + installation | 632 | 5 | **Port into Rust MiOS-MODULES** (ruling Q19), one lane per module: gate, gen, resolver, probe, serve, install, build, launch, node, wallpaperd. Verb paths become argv[0] links rendered at build. 4 non-Rust writers of `mios.toml` become `toml_edit` key patches. |
| Rust (`src/mios-rs` + `tools/native`) | 210 | 59 (24 inline) | One workspace and one lock. Six modules: `mios-core` (lib), `mios-gate`, `mios-gen`, `miosd`, `mios-serve`, `mios-desk`. Former binaries survive as applets. |
| automation | 160 | 15 → 11 | 30 data phases and 18 call phases become `[build.phases]` rows. 27 logic phases go to `phases.sh`, then to Rust. `build.sh` becomes `miosd build --run`. |
| AI plane (agent-pipe + `usr/lib/mios` + `libexec/ai`) | 501 | 55 | 16 sectioned domain modules (Law 14 keeps Python here). The 87 shims become one alias table plus a module finder, so every old import path keeps resolving. Tests become 17 suites, run per section in a subprocess. |
| docs | 650 | 117 → 40 | One volume per domain, holding verbatim page blocks keyed by original path. `mios-gen sync` renders each page back to its path, untracked and byte-identical, so the 1,089 doc references and all runtime readers stay valid. |
| tests + harness + misc | 392 | 51 | One suite per domain, one case per former file run in its own process, so test IDs and verdicts stay identical. Harness shims are rendered at dev setup, and projections tracked as source are rendered. |
| **mios.git** | **3,654** | **≈ 280–360** | Under the 500 cap with margin. |
| **mios-bootstrap** | **201** | **≈ 57** | `field/` assets become one tar. autorun, ventoy and i18n move into one module plus SSOT. The autounattend set is rendered at ISO build (Q12). Every published `irm\|iex` / `curl\|sh` path and every path deployed images read keeps working. |

### Foundations

These land first; every fold lane's proof depends on them.

1. **Root-overlay manifest.** A deterministic path, mode, sha256 and link listing of what the build installs, covering the Containerfile overlay and the git-checkout root. Without it, a fold has no proof that its runtime is unchanged.
2. **Render-twice projection gate with `MIOS_RENDER_ROOT`.**
   - The CI "generated artifacts match" step compares `git status`. Once projections are untracked it can no longer fail.
   - The Containerfile runs `miosd drift-check` before `mios-gen sync`.
   - Every scanner must read the rendered tree, or it scans nothing and passes.
3. **Verb-parity corpus.** Record each verb's argv, environment, exit code, stdout sha256 and stderr class, before any module fold.
4. **The Rust-categories gate sees every executable.** It must detect scripts by shebang, not extension: 267 verbs without an extension are invisible to it today.
5. **The docs volume engine and the virtual-corpus consumers.**
6. **The agent-pipe compatibility layer and section/AI-hint gate contract.** This also retires the line ceiling, per Decision 6.
7. **Test case-runner.** `ci-suites` and `run-suites` learn `path::case`.
8. **Rust dependency unification and one workspace.**
9. **mios-bootstrap renders after checkout** (Law 15). Hosts that update with `git pull` must get the rendered files too.

Domain order also changed. `harness` now precedes `docs`, so harness markdown is budgeted as harness.

### Open operator decisions

None of these blocks a fold; each fold keeps the behaviour until the operator decides.

- Law 16 templates live under `templates/`; may their bodies move into SSOT (`[templates.<t>].body`)?
- ADRs become one decision log (one page per ADR, bytes intact). This changes the ADR README's governance text.
- Code with no runtime caller: 146 verbs (93 reached only by tests, 26 only by docs, 27 by nothing), 35 agent-pipe root modules and 30 accelerator simulators. Port each as-is or retire it?
- `gateway-agent` has drop-ins but no base unit. Is it retired?
- The `.agents/workflows` command copies would be rendered at dev setup. Antigravity sessions that never run setup would lose them.
- MediCat leftovers in mios-bootstrap have no reader in either repo. Are they dead?
- Vendored dependencies: keep them as root-tree content or move them to a git-native store like `.archive`. Their OCI form is produced at publish time.

The per-area survey reports, with lane-sized tasks, planted negatives and risks, travel with the M9 fold assignments.

## Rationale

- **A ceiling must be able to fail.** The old `max_tracked_files` sat in `[drift.generated_ceilings]` and was re-baselined upward on every merge, from 3,050 to 3,662, so it bounded nothing. Shrink-only domains with a first-match partition are measurable per area, and a file that matches no domain is itself a violation. Silence is never a pass.
- **Folding beats deleting.** The operator's constraint is "no loss of functionalities". Deleting a file loses what it did, while folding keeps the behaviour and drops the file. The rendered-root manifest in Decision 5 proves this per lane. `.archive/` keeps every dropped byte recoverable, so even a fold that later proves wrong can be undone.
- **Projections are outputs because SSOT is the source.** A tracked copy of something `mios.toml` renders is a second source that can drift. Rendering in the build and gating on render-twice determinism removes about 524 files and a whole drift class at once.
- **Per repo, not per platform.** mios.git and mios-bootstrap are separate roots with separate histories and publishers, so each is measured on its own tree and its own budget is enforced on it. A combined number would let one repo's growth hide behind the other's shrinkage.
- **Git-native, not a bundle format.** The repo is the root, and git is already a content-addressed, compressed, sha256-capable store with a native overlay-to-root restore. A tarball or OCI layout committed into the tree would be a second packaging of the same bytes, opaque to the gates. OCI/ModelPack forms are produced at publish time from the tree (Decision 8).
- **Few large files over many small ones.** A module with an AI-hint and a section index is as navigable as a directory, and it keeps multicall MiOS-MODULES (ruling Q19) and the Rust static-binary port as the shape the tree converges to.

## Consequences

- **Large folds are hard to review.** Each fold lane moves code without changing it: one commit moves, a separate commit edits. Rename-aware history is lost, so the move commit's message lists every source → destination pair.
- **Tooling that reads one file per record must learn sections.** That covers AI-hint and manual indexing, `mios-gen`'s ADR index and the manual corpus ledger. Their phases change them first.
- **Lanes already in flight stay inside the budget.** They put new code in existing files rather than new ones.
- **Folding the vendored wheels changes the build's install step, not what is baked.** The same wheels install from one archive (Law 12).

## Sources

The operator's directives in session, 2026-10-09; the measurements in this record, from `git ls-files` at `ef7a0c69` plus this commit's deletions.
