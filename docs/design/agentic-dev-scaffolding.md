<!-- AI-hint: Researched recommendation for the agent-facing files and formats MiOS needs so autonomous development stays on track: one machine-selectable task queue, honest gates, context tiering, and the concrete schemas to adopt. -->
<!-- AI-related: docs/DOD.md, .devloop/LEDGER.md, TASKS.md, ROADMAP.md, AGENTS.md -->
# Agentic development scaffolding — research findings

> Produced by a parallel research sweep (six angles, a completeness critic, two gap-fills,
> one synthesis) against primary sources. Evidence quality is stated per claim in the final
> section; treat anything the report itself flags as thin as thin.

## Verdict

MiOS is missing a **queue**, not a process. Everything upstream of "what do I do next" is unusually mature — a constitution (16 Laws), 209 fitness functions with paired negative tests, an SSOT, a template-per-type scaffolder — and everything downstream of it is prose that no machine reads and no gate proves. The single highest-leverage gap is that **there is no per-task record with typed state that an agent can select from without loading the backlog, and no binding between a task's acceptance criteria and the drift-check ids that would prove them.** Every other named pain point is a symptom: two backlogs exist because nothing is authoritative; "surveyed vs done" is indistinguishable because done-ness is a word in a table cell rather than a receipt naming the check that passed; findings pile up because a finding has nowhere to go except the queue; the long bodies get re-read because the selector and the worker read the same file. The second gap is **honest gates** — an advisory skip and an empty-set match are both indistinguishable from a pass, and at 209 checks MiOS is more exposed to this than a small repo, not less. The third is **context tiering**: four always-loaded root files plus 40k lines of backlog is measurably a cost with no correctness dividend (ETH Zurich/LogicStar, arXiv 2602.11988: LLM-authored context files *reduced* success by 0.5–2% while adding 20–23% cost).

## What MiOS already does well

Do not rebuild any of these.

| MiOS has | Vindicated by |
|---|---|
| **16 numbered Laws as a non-negotiable tier above the work**, registered in `mios.toml [laws]` with `applies_to`/`enforced_by` | spec-kit's `memory/constitution.md` ("Constitution conflicts are automatically CRITICAL and require adjustment of the spec, plan, or tasks—not dilution"); Backlog.md's MANIFESTO.md; beads' PROJECT_CHARTER.md. MiOS's version is *stronger* than all three because theirs are prompt-advisory and MiOS's are gated. |
| **Paired negative test per check** (`tests/drift-gate-negatives.sh`) | This is the industry standard and MiOS is ahead of most of it: Semgrep Registry's `semgrep-rule-lints` CI job requires "at least one true positive finding and at least one true negative finding"; Checkov's `passing_resources`/`failing_resources`; Clippy's `tests/ui-cargo/<lint>/{fail,pass}`; ESLint `RuleTester` makes `valid`/`invalid`+`errors` structurally mandatory. The terraform-docs field failure — CI jobs that passed for months because `build` rejected `--check` and errored before comparing — is exactly what this discipline prevents. |
| **Regenerate-and-diff on derived files (Law 8, SSOT-PROJECTION)** | `hack/verify-codegen.sh` (Kubernetes), `write_source_files` diff_test (aspect_bazel_lib), terraform-docs `--check`. This is the only mechanism in wide use that *proves* a document still matches its source. |
| **Shrink-only ratchets on accepted debt** | ArchUnit `FreezingArchRule` + `ViolationStore`, Betterer, imbue-ai/ratchets, PHPStan baselines. (The ceilings need fixing — see Gap 6 — but the mechanism is right.) |
| **ONE-TEMPLATE-PER-TYPE + `mios new <type>`** | OpenSSF Scorecard's `checks/write.md` add-a-check contract; Clippy's `cargo dev new_lint` (emits lint + UI test + registry entry in one command). MiOS already owns the hardest part of this. |
| **ADR corpus with YAML frontmatter + index generator** | MADR 4.0; Nygard's monotonic-never-reused numbering. |
| **ROADMAP workstream frontmatter carrying `laws`/`ssot_keys`/`adr`/`deps`/`acceptance`** | Kubernetes `kep.yaml` (machine-readable sidecar beside human prose). MiOS invented this independently and should extend it *downward* to tasks rather than sideways. |
| **Law 12 BAKE-NOT-FETCH + building its own image** | This is the hermetic-environment answer (pre-commit's `additional_dependencies` model) already sitting in the repo unused for gates — see Gap 2. |

## The gaps, ranked

---

### Gap 1 — One queue: per-task files with typed frontmatter, written only by a CLI

**Missing.** A task record a machine can select, claim, and close without reading the other 1,999 tasks.

**Failure it causes.** Two competing backlogs (~40k lines). No single queue. Long-form bodies re-read in full by every agent. Selection is agent judgement over prose, so two sessions can pick the same work or contradictory work.

**Pattern.** Backlog.md's one-file-per-task with typed YAML frontmatter and HTML section markers, plus Kubernetes' typed sidecar principle (`kep.yaml` for machine state, `README.md` for the human argument), plus beads' ready-set query semantics. Every project that outgrew a flat markdown backlog split the record in two; none scaled a single long-form prose record as both the human rationale and the machine state.

**Concrete.** `usr/share/mios/tasks/task_962.md` — under `/usr` because Law 1 puts vendor/static config there and because it bakes the queue into the image, so a deployed host's own agents can read it. (`backlog/` at repo root would land at `/backlog`, which is not FHS.)

```markdown
---
id: task_962                       # PRESERVED from TASKS.md; never reminted
title: Render Quadlets from SSOT templates
status: ready                    # draft|ready|claimed|blocked|done|expired|superseded
priority: P0
domain: quadlets
workstream: WS-DOCGEN
laws: [8, 16]
ssot_keys: ["templates.quadlet"]
adr: [ADR-0012]
deps: [task_946]                   # must be < this id (forward-reference ban)
discovered_from: null            # set => this is a finding, not planned work
scope:                           # declared write-set; gate checks the diff against it
  - usr/share/mios/templates/quadlet.template
  - automation/34-render-quadlets.sh
acceptance:
  - id: task_962-AC1
    text: Every rendered Quadlet is byte-identical to its regeneration
    checks: [check_quadlet_ssot_projection]        # <- THE JOIN
  - id: task_962-AC2
    text: A hand-edited Quadlet fails the gate
    checks: [check_quadlet_ssot_projection_neg]
why: >
  In the context of hand-edited Quadlets drifting from SSOT, facing Law 8,
  we decided to render them at bake and neglected runtime templating,
  to achieve a diffable gate, accepting that a Quadlet edit needs a rebuild.
created: 2026-01-04
updated: 2026-09-14
wait: null                       # hidden from ready-set until this date
until: 2027-03-01                # auto-expires; a task can be designed to die
receipt: null                    # sha256 of the closure receipt; set by `mios task close`
external_ids: ["tasks-md:task_962"]
---

<!-- SECTION:PLAN:BEGIN -->  … read only by the agent that has CLAIMED this task
<!-- SECTION:PLAN:END -->
<!-- SECTION:NOTES:BEGIN -->  … append-only, written via `mios task note`
<!-- SECTION:NOTES:END -->
```

Two hard rules, both borrowed verbatim from Backlog.md's agent guidelines and both Law-8-shaped: **the file is never hand-edited** (writes go through `mios task`, a Rust static binary in `tools/native/mios-task/` per the standing directive), and **the selector reads only frontmatter** — the body is progressive disclosure, loaded once claimed.

The `checks:` field on each acceptance criterion is the highest-value single token in this whole report. It is MADR 4.0's `## Confirmation` section made machine-readable, and MiOS is the only repo I saw in this corpus with a 209-entry named-check registry to point it at.

---

### Gap 2 — Honest gates: a skip, an empty match, and a missing tool must all be RED

**Missing.** A gate that cannot report green without proving it ran.

**Failure it causes.** "Gates that advisory-skip when a tool is missing, so a local run reports green while proving nothing." At 209 checks this compounds in a second way nobody has flagged: a check whose selector silently stops matching (renamed dir, moved file, changed glob) reports green forever, and its output is byte-identical to success.

**Patterns, all four of which MiOS can implement today:**

1. **Empty-set-pass guard.** ArchUnit's `archRule.failOnEmptyShould=true`, default-on since 0.23.0: a rule evaluated against zero subjects is an error. Generalised: every check counts its candidate set and exits non-zero with `RULE CHECKED NOTHING` at zero. Per-check opt-out only, with a written justification in SSOT — the way Law 6 already handles root Quadlets.
2. **Exact-count assertion.** pytest's exit-5 catches total discovery collapse but not partial. MiOS has a registry, so the stronger form is free: `just drift-gate` reads the expected check count from `mios.toml [checks]` and fails if the number *executed* differs. One assertion closes both empty-set-pass and skip-as-pass for the whole suite.
3. **Hermetic environment.** pre-commit owns its tools' installation so there is no "tool missing" branch to skip on. MiOS builds a container image and already has Law 12. Run the gate *inside the built image* and delete every `command -v foo || { echo skip; exit 0; }`.
4. **Tri-state result, aggregated pessimistically.** OpenSSF Scorecard assigns `-1` when a check cannot run and **excludes it from the aggregate** "to avoid the penalty of failing a check" — that is precisely MiOS's pathology, shipped as a feature. Borrow Gemara Layer 4's enum (`NotRun | Passed | Failed | NeedsReview | NotApplicable | Unknown`) and invert the aggregation: `NotRun` makes closure fail.

**Also change what MiOS already has:** the central `tests/drift-gate-negatives.sh` is itself a drift surface — a check can be added without its negative and nothing notices. Every primary source that mandates paired fixtures co-locates them (Semgrep, Checkov, Clippy, ESLint). Move to `tests/fixtures/<check-id>/{must-pass,must-fail}/`, add a `drift-check` type to `usr/share/mios/templates/` so `mios new drift-check` emits check + both fixtures atomically, and keep one central meta-check asserting `count(checks) == count(must-fail fixtures)`. And **assert the diagnostic id, not the exit code** — with 209 checks, a negative fixture that merely turns the suite red proves almost nothing, because any of the other 208 could be the one firing.

---

### Gap 3 — Done-ness as a machine-emitted receipt bound to a commit

**Missing.** Proof, rather than assertion, that a task's acceptance criteria were checked.

**Failure it causes.** "No mechanism distinguishes 'surveyed' from 'done'." This is not a hypothetical risk: Terminal Wrench (arXiv 2604.17596) ranks **output-spoofing — "fabricates expected outputs without computing them" — as the #2 exploit category by trajectory volume (1,071)**, behind hollow-implementation ("passes tests but implements no real logic", 2,243). An agent writing "103/103 tests passed" into a task body is that category, and post-hoc detection is the weakest available control (AUC drops from 0.97 to 0.92 once chain-of-thought is stripped, which is the regime a repo reviewing only committed artifacts operates in).

**Pattern.** The gate runner emits the evidence; the agent cannot author it. in-toto's Test Result predicate is the exact schema — its subject can be a git commit and its predicate carries **arrays of test names**, not an aggregate.

**Concrete.** `tools/native/mios-gate/` writes, on every `just drift-gate`:

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{ "name": "mios-drift-gate",
                "digest": { "gitCommit": "22a091b5f0…" } }],
  "predicateType": "https://in-toto.io/attestation/test-result/v0.1",
  "predicate": {
    "result": "PASSED",
    "configuration": [{ "name": "usr/share/mios/mios.toml",
                        "digest": { "gitBlob": "e3b0c442…" } }],
    "url": "file:///var/log/mios/drift-gate/<run-id>.log",
    "passedTests": ["check_quadlet_ssot_projection", "check_law6_user_group", "…"],
    "warnedTests": [],
    "failedTests": ["check_cdi_spec_render (NOT_RUN: nvidia-ctk absent)"]
  }
}
```

Note the deliberate choice: **a NotRun check goes in `failedTests` with a reason**, because the in-toto predicate has no NotRun state and silently dropping it is the Scorecard bug. Store via `git notes --ref=mios/receipts add -F receipt.json <sha>` — offline, no server, no history rewrite, degrades open. **Be honest that git notes is a transport, not a trust boundary** (anyone with push access to `refs/notes/*` can write one); the signature by the gate binary is what is trusted, and for a local-first air-gappable OS that must be a host key or a `cosign --bundle`-style offline verification, not keyless Fulcio/Rekor.

Then add check #210, `check_task_closure`, with its negative fixture:

> For every task with `status: done`: resolve `receipt`; assert (a) the receipt's `gitCommit` is an ancestor of HEAD; (b) no file in the task's `scope` has changed since that commit (Bazel's input-digest model — this is what stops a done task silently becoming undone without re-gating the world on every unrelated commit); (c) `union(acceptance[].checks) ⊆ predicate.passedTests`; (d) `result == "PASSED"`.

Cheap first version if the full receipt is too much: skip signing entirely. An **unsigned JSON receipt written by the gate runner rather than summarised by the agent**, plus assertion (c), gets most of the value on day one.

---

### Gap 4 — A findings lane that is not the queue

**Missing.** Somewhere for a mid-task discovery to go that is neither an inline note nor a ready-set row.

**Failure it causes.** "Findings pile up faster than fixes." This is the *expected output* of any reviewer pass, not a sign of a broken repo — Anthropic's own guidance says so plainly: "A reviewer prompted to find gaps will usually report some, even when the work is sound, because that is what it was asked to do."

**Pattern.** beads' typed `discovered-from:` edge plus Backlog.md's four sibling lanes (`tasks/` / `drafts/` / `docs/` / `decisions/`). MiOS already has the decisions lane (ADRs) and the docs lane (`usr/share/doc/mios/`); it needs `drafts/`.

**Concrete.** A finding is created as `usr/share/mios/tasks/drafts/<id>.md` with `status: draft` and `discovered_from: task_962`. Drafts are invisible to `mios task ready`. Promotion (`mios task promote <id>`) moves the file into `tasks/` and is the only way a finding enters the work queue. Two consequences worth the trouble: the finding-to-fix ratio becomes a countable number (drafts opened vs promoted vs closed per session) that can itself be ratcheted, and the parent task closes on its own acceptance criteria without carrying the discovery.

Also add the **admission budget** and the **expiry**: a draft with no promotion and no touch within N days moves to `archive/` with reason `stale`, mirroring `actions/stale`'s label-then-close-then-reset-on-activity timer, implemented as a drift check over `updated`. Archived, searchable, *never deleted* — the stale-bot backlash literature (pypa/virtualenv#1311, jestjs/jest#12496) is unanimous that closing an unresolved report "gives a false sense of buglessness".

---

### Gap 5 — Context tiering: shrink the always-loaded layer, defer the rest

**Missing.** Any mechanism that makes an instruction cost zero until it is relevant.

**Failure it causes.** Four root agent files describe the build pipeline, the AI plane, the Blade invariant, 16 Laws and global code form to every agent on every session, whether it is editing a Quadlet or a Python test. Vendor caps are an order of magnitude below where MiOS sits: Windsurf enforces 6,000 chars global / 12,000 per rule; Copilot says "no longer than 2 pages"; Claude Code targets under 200 lines; Agent Skills recommends SKILL.md under 500 lines / ~5,000 tokens. And the four files *stack* rather than override — Claude Code and Gemini CLI concatenate root + ancestors, and the docs warn that on contradiction "Claude may pick one arbitrarily."

**Concrete, in order of payoff:**

1. **Collapse the four files to one canonical + three deltas** using the literal import, not prose: `CLAUDE.md` becomes `@AGENTS.md` on line 1 plus a `## Claude Code` section. (Not a symlink — MiOS ships a Windows operator flow and symlinks need Developer Mode.) Delete or reduce `MiOS.md`.
2. **Generate the derivable regions.** The Laws table, the `[ports]` table, the `just`-target list and the layer-resolution order are all projections of `mios.toml` and the justfile. Emit them between markers — `<!-- BEGIN MIOS CONTRACT v:1 hash:bacef91e -->` … `<!-- END … -->`, the beads/terraform-docs idiom — and guard with the existing regenerate-and-diff harness. That splits the root file into a *proven* half and an *unproven* half and shrinks the unproven half to something auditable.
3. **Apply the /doctor heuristic to what remains:** cut what the agent can derive from the tree (directory layouts, dependency lists, architecture overviews); keep pitfalls, rationale, and conventions that differ from tool defaults. The Blade hardware invariant and the two-repo division of labour stay; the build-pipeline diagram goes.
4. **Move subsystem rules to path-scoped files.** `.claude/rules/automation.md` with `paths: ["automation/**/*.sh"]`, `agent-pipe.md` with `paths: ["usr/lib/mios/agent-pipe/**"]`, `native.md` with `paths: ["tools/native/**/*.rs"]`. Nested files must be **additive and self-contained** — the agents.md FAQ says nearest-file-wins but Codex, Claude Code and Gemini CLI all concatenate, so a nested partial override is correct under one reading and contradictory under the other.
5. **Move the "confirm before" list out of prose into enforcement.** `git push`, `bootc switch`, `dnf install`, `rm -rf`, `git reset --hard`, `podman machine rm` are currently a paragraph, which the vendor explicitly says prose cannot enforce: "To block an action regardless of what Claude decides, use a PreToolUse hook instead." They belong in `permissions.deny`/`permissions.ask`. For MiOS specifically, the FHS-correct home is **`/etc/claude-code/` managed settings**, which project settings cannot override and which MiOS already ships the `/etc` overlay for. This also shortens the root file, paying down (1).
6. **Procedures become skills, not prose.** The 209 drift checks, `mios new <type>`, the ADR workflow and the build pipeline are procedures. Under the Agent Skills three-tier load, an agent pays ~100 tokens to *know* a procedure exists and the full cost only when it runs it. MiOS already has ONE-TEMPLATE-PER-TYPE, so a `SKILL.md` template under `usr/share/mios/templates/` declared in `[templates.skill]` fits Law 16 unchanged.

---

### Gap 6 — Fix the ratchets before they eat any more rationale

**Missing.** An audited escape hatch, and identity-based rather than count-based ceilings.

**Failure it causes.** "Ratchets that punish explanatory comments, so agents compress rationale out of code to stay green." This is textbook Goodhart *and* it maps onto Terminal Wrench's keyword-gaming category run in reverse. It is also inverted against every vendor's stated position: the /doctor heuristic and the auto-memory rules both say **rationale is the category to preserve**.

**Three changes:**

1. **Never ratchet a measure whose cheapest improvement is deleting information.** A ceiling correlated with comments or docstrings must be re-scoped or dropped. This is not negotiable against any of the source material.
2. **Migrate ceilings from integers to identity sets.** A scalar ceiling cannot distinguish a new violation from a legitimate addition and permits substitution (fix one, add one, count unchanged). ArchUnit's `ViolationStore` and Betterer's results file both store identities (file + rule + normalised message), not counts.
3. **Add per-entry staleness detection.** PHPStan's `reportUnmatchedIgnoredErrors` is **on by default**: when a suppressed finding stops reproducing, the now-unmatched entry is a *failure* and must be deleted. That is the mechanism that makes a register genuinely shrink, and it is also a free "surveyed vs done" signal — an entry that no longer reproduces IS done and the tool says so. mypy `--warn-unused-ignores` and ESLint `reportUnusedDisableDirectives` are the same idea.
4. **Add a `bump` with recorded justification**, the way Law 6 already allows root Quadlets with a justification in `[security.privileged_quadlets]`. imbue-ai/ratchets has `ratchets bump <rule> --region <path> --count <n>`; **I could not corroborate the claim that it enforces justification in the commit message** — treat that as convention, and make MiOS's version enforce it in SSOT.

Also borrow ArchUnit's **CI write-lock**: `freeze.store.allowStoreUpdate=false` in the image-build path. Without it an agent under pressure regenerates the baseline and the ratchet becomes a rubber stamp.

---

### Gap 7 — Bind the ADR corpus to the check registry, and gate its graph

**Missing.** Any mechanical relationship between a decision and the check that enforces it, and any lifecycle validation on the ADR corpus.

**Failure it causes.** "The task list, the roadmap and the ADRs are three overlapping records of intent that can disagree." They disagree because they carry independent id spaces and nothing can join them.

**The honest negative finding first: nobody machine-checks that code still matches its ADR.** Every validator surveyed — kepval, adrs-core lint, mdbook-lint, pyadr, adr-tools — validates the *record*, never the implementation. MADR 4.0's `## Confirmation` section asks a human to *name* a fitness function in prose and never checks that it exists. So MiOS is not adopting this; it is **building** it — and it is the only repo in this corpus positioned to, because it already has 209 named checks.

**Concrete.** Add to ADR frontmatter:

```yaml
status: accepted            # closed enum, not MADR's free string
enforced_by: [check_quadlet_ssot_projection]
supersedes: [ADR-0009]      # ascending, IETF-style
superseded_by: null
review_by: 2027-01-15       # provisional decisions expire
applies_to: ["automation/**", "usr/lib/mios/quadlets/**"]
```

Then five cheap checks with paired negatives: (a) every `accepted` ADR names ≥1 `enforced_by` id; (b) every named id exists in the check registry; (c) every check id is named by ≥1 ADR or Law (an unjustified check is a check nobody can explain); (d) supersession reciprocity — if A supersedes B, B must name A — plus dangling-target and cycle detection; (e) `review_by` in the past with a provisional status is a hard failure. The motivating defect in the one repo that built this (rjmurillo/ai-agents) is instructive: **59 of 98 records had no machine-readable status**, collapsing retired ADRs into "proposed", "making binding constraints unverifiable."

Add the **anti-dilution clause** to the Laws registry, verbatim in spirit from spec-kit: a conflict between work and a Law is resolved by changing the work, never by weakening the Law; a Law change is a separate explicit act. MiOS's law list does not currently say this, and it is the precise counter to an agent resolving a Law/work conflict by degrading the work.

And use `applies_to` at the diff: the gate resolves the changeset against ADR path globs and *prints the governing ADRs*. Filing is not surfacing — an indexed ADR corpus that agents do not read at the moment of relevance is the failure MiOS is already living. The mechanisms that actually surface a decision fire off the changeset, not off a website.

---

### Gap 8 — Session close protocol (the research does NOT support a HANDOFF.md)

**Missing.** A guaranteed end-of-session ritual.

**What the research rules out.** A prose `HANDOFF.md`/`SESSION_LOG.md` is a dead end — it would be a fifth overlapping record of intent, hand-authored so no generator can diff it, with nothing to expire it. There is no primary specification for it; the material is blog-level and mutually inconsistent on naming. **Compaction is also not a handoff:** in SWE-Marathon, 0 of 71 reward-bearing summarizer trials passed against 8.9% for trials without it — "compaction tracks failure rather than rescue."

**What it supports.** beads' "Landing the Plane": a numbered mandatory protocol in `AGENTS.md` that reconstitutes the next session's context from *queue state* plus one sentence, with no new file. Adapted to MiOS: (1) file drafts for remaining findings; (2) run `just drift-gate` — **and if a gate is broken or skipped, file a P0 task, do not print a warning**; (3) `mios task close` / `mios task release`; (4) commit and push; (5) end by naming the next task id. Step 2 is the structural fix for advisory-skip at the session boundary: the skip itself files work.

Bind it mechanically rather than by convention: a `Stop` hook running `just drift-gate` so a session cannot end red, and a `SessionStart` hook (matcher `startup|resume|clear|compact`) that `cat`s `mios task ready --json`. If you want a progress log at all, make it the capped `MEMORY.md` shape — index ≤200 lines, one line per entry, detail in on-demand topic files, `modified` timestamp, and an **error on overflow that tells the agent to compact it** — not free prose. Keep it under `/var/lib/mios/ai/memory/` per existing convention, and accept that it is therefore machine-local and cannot be the cross-session contract. The committed queue plus git history *is* the contract.

---

## Proposed file layout

```
/                                               # repo root IS the deployed system root
├── AGENTS.md                          CHANGE   Canonical agent contract. ≤200 lines. Generated
│                                               regions (Laws/ports/just targets) between markers.
├── CLAUDE.md                          CHANGE   `@AGENTS.md` + Claude-only deltas. Nothing else.
├── GEMINI.md                          CHANGE   `@AGENTS.md` + Gemini-only deltas.
├── MiOS.md                            DELETE   Fourth copy of the identity contract; collapse into AGENTS.md.
├── ROADMAP.md                         EXISTS   Workstream tier. Tasks cite it; it no longer duplicates them.
├── TASKS.md                           CHANGE   Frozen, checksum-locked, becomes a generated projection
│                                               that drains monotonically to a redirect stub.
├── AGY-TASKS.md                       CHANGE   Frozen, drains to zero, then deleted.
├── .gitattributes                     CHANGE   `TASKS.md linguist-generated=true` (collapse from diffs).
├── etc/claude-code/
│   └── managed-settings.json          NEW      permissions.deny/ask for the confirm-before list.
│                                               FHS-clean, cannot be overridden by project settings.
├── .claude/
│   ├── settings.json                  NEW      SessionStart hook (cat `mios task ready`), Stop hook
│   │                                           (just drift-gate), InstructionsLoaded logger.
│   └── rules/
│       ├── automation.md              NEW      `paths: ["automation/**/*.sh"]` — numbered build steps.
│       ├── agent-pipe.md              NEW      `paths: ["usr/lib/mios/agent-pipe/**"]` — Python AI plane.
│       ├── native.md                  NEW      `paths: ["tools/native/**/*.rs"]` — Rust target rules.
│       └── templates.md               NEW      `paths: ["usr/share/mios/templates/**"]` — Law 16.
├── usr/share/mios/
│   ├── mios.toml                      CHANGE   + [tasks], [tasks.urgency], [closure], [checks],
│   │                                           [migration.frozen], [laws].anti_dilution
│   ├── tasks/
│   │   ├── task_962.md                  NEW      One file per task. Frontmatter = machine state,
│   │   │                                       body = human rationale, read only once claimed.
│   │   ├── drafts/D-0007.md           NEW      Findings lane. Invisible to the ready-set.
│   │   ├── archive/2026-09-14-task_946/ NEW      Dated archive on close/expire. Never deleted.
│   │   └── .migration-map.tsv         NEW      Append-only old-id → new-id, doubles as resume log.
│   ├── templates/
│   │   ├── task.md                    NEW      `[templates.task]` — `mios new task`.
│   │   ├── drift-check.sh             NEW      `[templates.drift_check]` — emits check + BOTH fixtures.
│   │   └── skill/SKILL.md             NEW      `[templates.skill]` — progressive-disclosure procedures.
│   └── ai/skills/<name>/SKILL.md      NEW      Drift-check authoring, ADR workflow, build pipeline.
├── usr/share/doc/mios/adr/NNNN-*.md   CHANGE   + enforced_by / superseded_by / review_by / applies_to.
├── tools/native/
│   ├── mios-task/                     NEW      Rust static binary. SOLE writer of the queue.
│   │                                           `ready --json`, `claim`, `note`, `close`, `promote`,
│   │                                           `import --dry-run`, `export --legacy`.
│   └── mios-gate/                     NEW      Rust static binary. Runs the 209 checks, emits the
│                                               receipt, asserts executed-count == registry-count.
├── automation/98-drift-checks.sh      CHANGE   + check_task_closure, + empty-set guard per check,
│                                               + ADR graph checks, + frozen-file checksum check.
├── tests/
│   ├── fixtures/<check-id>/must-pass/ NEW      Co-located; replaces the central negatives file.
│   ├── fixtures/<check-id>/must-fail/ NEW      Asserts the DIAGNOSTIC ID, not the exit code.
│   └── drift-gate-negatives.sh        CHANGE   Reduced to the meta-assertion: counts must match.
└── docs/design/                       EXISTS   Unchanged. Tasks cite; they do not restate.
```

## The one-task selection problem

**Format.** As Gap 1. One file per task under `usr/share/mios/tasks/`, filename `task_962.md` — the id only, **never the title**, because a retitle would otherwise rename the file and break every path-based reference (this is the concrete reason to reject Backlog.md's `back-355 - Some-Title.md` scheme).

**How an agent picks exactly one task.** The session's first command is `mios task ready --json`. The ready-set is a graph query over frontmatter alone — never a document read:

```
ready = { t : t.status == "ready"
              ∧ ∀d ∈ t.deps : d.status ∈ {done, superseded}
              ∧ (t.wait == null ∨ t.wait ≤ today)
              ∧ (t.until == null ∨ t.until > today) }
```

Ranked by an urgency polynomial whose coefficients live in `mios.toml [tasks.urgency]` (Law 7: "which task next" is operator-tunable, not agent judgement) — Taskwarrior's model, e.g. `priority = 12.0`, `blocking = 8.0`, `law_p0 = 15.0`, `age = 2.0`. Then `mios task claim task_962` sets status + assignee in one operation and commits, so two concurrent sessions cannot take the same row. The agent reads exactly one body: the one it claimed.

`wait:` is the sharpest tool here and the most overlooked: a task that is real but not yet actionable is **invisible rather than deleted**, so the ready-set is small by construction and self-heals on a date. That is the direct answer to "the queue is too long to read."

Two checkable invariants worth their own drift checks: **no forward references** (a task may only depend on ids lower than its own — Backlog.md's rule, and the cheapest guarantee that the dependency graph stays acyclic), and **one task = one context window = one reviewable diff**. MiOS's 209 gates make per-task PRs gate-heavy, so keep the existing `drift-gate` (fast, no image) / `build` (slow) split; the sizing rule only holds if the per-task gate run is fast.

**How done-ness is proven.** As Gap 3. Not by the status field. `mios task close task_962` refuses unless (a) a receipt exists for a commit that is an ancestor of HEAD, (b) nothing in `scope` changed since it, (c) `union(acceptance[].checks) ⊆ receipt.passedTests`, (d) `result == PASSED`. The status field becomes a *consequence* of the receipt, not a claim. `check_task_closure` re-verifies all of this from scratch at gate time, so a hand-flipped status is a red build.

**How findings are distinguished from fixes.** `discovered_from:` set ⇒ the record lives in `drafts/` and is invisible to `ready`. Promotion is an explicit, logged act. The count of open drafts, and the drafts-opened-vs-promoted ratio per session, are numbers — ratchet them. Nothing else in this corpus makes "findings outrunning fixes" measurable rather than felt.

**How the two backlogs migrate losslessly.** Nine steps, in order. This is a long-horizon job and should be budgeted as one.

1. **Dry run first.** `mios task import --dry-run` emits counts by source and verdict, creation order respecting `Dep:` edges, duplicate ids across the two files, unresolvable dependency targets, and every `T-\\d+` cited in ROADMAP/ADRs/commits with no matching row. "Anything that cannot map" is the single most valuable output of the whole migration — it *is* the real scope of the three-way disagreement. (Spring ran the real import first and generated "tens of millions of emails"; GitHub support had to intervene.)
2. **Preserve ids.** `task_962` becomes the new record's primary key. No alias table, no rewrite of existing citations. AGY rows carry `external_ids: ["agy-tasks:task_1255"]` and, where merged, `merged_from: ["task_1255"]`.
3. **Map file as resume log.** `usr/share/mios/tasks/.migration-map.tsv`, append-only, written per batch so an interrupted 40k-line run resumes as a set-difference instead of duplicating.
4. **Block, don't compare all pairs.** 3–10 blocking rules over *structured* keys, not titles: shared `ssot_keys` entry; shared law id + domain; shared referenced path; shared ADR id; normalised-title prefix. Count comparisons per rule *before* running. Title similarity alone will fail here — the two files were written by different authors, which is exactly the textually-dissimilar duplicate case where detection "performance is significantly poor" (92,854-report study, arXiv 2212.09976), and even a tuned domain system tops out around 87% precision.
5. **Threshold, then human remainder.** Mozilla's BugBug shape: auto-apply above a confidence threshold recorded in `mios.toml` (start high, ~0.85); everything below lands in a review queue *sized and reported as a number*. Ship when the auto-tier precision clears the bar, not when the agent finishes reading.
6. **Merges are non-destructive and reversible.** Both external ids retained, both bodies archived, `merged_from` recorded with `merge_confidence` and `merge_method`.
7. **Rewrite cross-references from the map**, mechanically, across ROADMAP.md, ADR frontmatter and docs — then a drift check that greps for any surviving `T-\\d+` that is neither a live id nor a map key.
8. **Freeze the old files — this is the load-bearing half.** OpenStack is the documented counterexample: it migrated Launchpad → StoryBoard but left the lock to each project, and the outcome is recorded verbatim — "a lot of people just abandoned the idea of task tracking" across a permanent mix of trackers. Freeze with a checksum ledger in `mios.toml [migration.frozen]` (`{sha256, frozen_at_commit, superseded_by}`), a Flyway-style validate check, a Go-style `<!-- Code generated by mios task export --legacy; DO NOT EDIT. -->` first line, and `linguist-generated=true`. The marker and the gitattribute are advisory *labels*; the checksum check is the enforcement. Add a **shrink-only ratchet on legacy line count** so the projection drains monotonically — MiOS already has the idiom.
9. **Update `AGENTS.md` in phase 1, not phase 3.** Fowler's culture caveat applies exactly: if the agent entry files still name `TASKS.md` as the backlog, the new queue becomes backlog #3 regardless of any gate.

Audit the triage pass itself with a stratified sample (strata = verdict × confidence band; ~100–385 items depending on the margin you want), **oversampling the `expire` stratum** because it is the only irreversible verdict. Commit the sample and its labels; record the resulting per-stratum precision as a ratchet.

## What to ignore

| Pattern | Why it dies here |
|---|---|
| **GitHub Issues (or any remote tracker) as the primary queue** | Breaks three load-bearing invariants: the repo root IS the deployed system root and "git pull must restore full context"; the 209 checks run offline and cannot gate on a remote API; Law 12 has no egress guarantee. Copilot's coding agent also only sees the issue *as of assignment* — a snapshot, which is the wrong shape for a multi-session queue. Keep Issues as a human inbox triaged into the in-repo queue. |
| **An embedded DB as the SSOT** (beads' Dolt at `.beads/embeddeddolt/`) | Beads is explicit that `issues.jsonl` is "an export… not the source of truth." Excellent engineering, wrong for a repo whose premise is that the git tree IS the system. Take the semantics (ready-set, atomic claim, typed edges); reject the store. |
| **A single JSON/TOML registry for the whole backlog** (Task Master's `tasks.json`) | Recreates TASKS.md's problems in a format humans read worse: every edit rewrites one file, concurrent agents conflict, diffs are unreadable, and selection still requires loading the array. `mios.toml` at 11,700 lines is the same lesson from the other direction. Take the field set (especially `testStrategy`); reject the storage. |
| **`HANDOFF.md` / `SESSION_LOG.md` / `.claude/handoffs/` prose** | A fifth overlapping record of intent, hand-authored, unexpiring, with no primary specification anywhere. |
| **Compaction as the handoff mechanism** | 0 of 71 reward-bearing summarizer trials passed, vs 8.9% without (SWE-Marathon). Anthropic: "compaction isn't sufficient." |
| **`llms-full.txt`** | Anthropic's own is ~481,349 tokens against ~8,364 for `llms.txt`. Generating one for a 128-page manual recreates the 20k-line problem under a new name. The curated index is worth generating; the full variant is not. |
| **`@path` imports as a context-size fix** | Explicitly rejected by the vendor: "Splitting into @path imports helps organization but doesn't reduce context, since imported files load at launch." Only glob-scoped rules and skills actually defer cost. |
| **`CLAUDE.local.md`** | Gitignored, per-worktree, invisible in every diff — an untraceable source of behavioural divergence between sessions in exactly the repo that can least afford it. |
| **Auto-generating the instruction files wholesale** | Measured net-negative: −0.5% to −2% success, +20–23% cost (arXiv 2602.11988), and the generated content was largely redundant with existing docs. Generate the derivable *fragments* under a diff gate; hand-curate the rest. |
| **A `constitution.md` layer** (spec-kit) | MiOS's 16 Laws already do this, gated rather than prompt-advisory. Adding one creates a second rule set that can disagree with the first — the exact pathology. Take spec-kit's read-only cross-artifact analyzer; leave the constitution. |
| **Full mutation testing** | No operator sets exist for TOML, systemd units, Quadlet templates or bash glue — i.e. most of the repo — and the surviving-mutant report is a third unbounded findings backlog. The negative-control form gives the same guarantee at O(1) per check. |
| **Per-worktree parallel fan-out as-is** (`/batch`, Copilot branches) | Assumes a normal checkout. `.git` IS `/`. The bounded-session and one-task-one-PR halves transfer; worktree isolation needs a MiOS-specific design (container-per-task against the OCI image is the obvious candidate). |
| **Sequential numeric ids allocated by scanning the tree** for *new* work | Two agents in the same window compute the same next number and collide at merge; spec-kit has had to bolt on `--number`/`--timestamp` escape hatches. Grandfather `T-####`, but allocate new ids through the CLI's lock (or hash them). |
| **Semgrep-style `todoruleid:` indefinite snoozes** | A suppression with no owner and no expiry is "surveyed, never done" formalised into tooling. |
| **Count-based ratchet ceilings** | Cannot distinguish a new violation from a legitimate addition and permit substitution. Identity sets instead. |
| **Stale-bot mass-close as the expire mechanism** | Documented backlash across pypa/virtualenv#1311, jestjs/jest#12496, acts-project/acts#578. Archive with reason, keep searchable. |
| **e-ADR / comment-borne decision logs** | Java annotation machinery against Rust/Python/TS targets, and MiOS's ratchets are actively selecting against explanatory comments — the pattern would be introduced into an environment engineered to destroy it. Fix the ratchet first; then a single machine-parseable `ADR-0012` header token, load-bearing for a gate, is the survivable residue. |
| **A fitness-function dashboard** | Assumes multiple human teams on a scoreboard. One operator, agents reading terminal output. The leverage is in a machine-readable registry the checks project themselves into. |
| **Taskwarrior / Kiro / spec-kit as *tools*** | Data outside the repo (Taskwarrior `~/.task`), a proprietary IDE (Kiro), a young toolkit whose layout has already changed across versions (spec-kit). All three have portable conventions worth copying. None is a dependency an offline-baked OCI image can take. |
| **OpenSpec's "archive warns but does not block"** | Right for a human-steered tool, wrong here: an unblocked warning is indistinguishable from a pass and no human reads it. Adopt the dated-archive mechanism; make incomplete acceptance criteria a hard failure. |

## Confidence and sources

**Well-evidenced and safe to build on.** The paired-fixture discipline (multiple independent implementations: Semgrep, Checkov, Clippy, ESLint `RuleTester`). Regenerate-and-diff as a doc gate (Kubernetes, Bazel/aspect, terraform-docs — *including* its documented field failure, which is the best argument for MiOS's negative tests). Empty-set-pass as an error (ArchUnit, default-on since 0.23.0). Stale-suppression-is-an-error (PHPStan default, mypy, ESLint). Monotonic-never-reused ADR numbering (Nygard, 2011, universal). Typed sidecar beside human prose (Kubernetes `kep.yaml`, validated by `kepval`). Bidirectional supersession and derived reverse edges (adr-tools; IETF `Obsoletes:`/`Updates:` over ~9,700 documents since 1969). in-toto Test Result and SLSA VSA/Provenance predicate schemas (vetted specs, quoted field-by-field). Progressive disclosure caps (five vendors, consistent numbers). Blocking rules for entity resolution (Splink/dedupe/Zingg, standard Fellegi-Sunter practice). The context-degradation measurements (Chroma, 18 frontier models).

**Thin, but directionally sound.** The ADR-lifecycle gate (rjmurillo/ai-agents PRs #5209/#5285) is *one repository's implementation*, not a standard — adopt the check list, not the code. `review_by` expiry is anecdotal. The tool-receipts literature (arXiv 2603.10060, 2512.17259, 2607.13716) is all 2026 preprints. Self-Harness's held-in/held-out acceptance rule is a single paper on a 64-task subset. The stratified-audit sample sizes are textbook Cochran plus the researcher's own synthesis, not a backlog-triage standard.

**Flagged as uncorroborated or self-contradictory — do not build on these without re-reading the primary source.**

- **The GitHub skipped-check contradiction is the most dangerous item in the corpus.** One source says a skipped job reports SUCCESS and satisfies a required check; another quotes GitHub saying a skipped workflow "stays in a Pending state and blocks merging." Both are stated as fact. The real distinction — a job skipped by a job-level `if` reports a conclusion; a workflow that never triggers because of path filters never reports at all — is never drawn. Any aggregate-gate design depends entirely on which case applies. **Verify before implementing.**
- **Terminal Wrench's taxonomy is misreported**: eleven categories, not ten, with `deceptive-rationalization` silently dropped. Only the top two counts are confirmed; the per-category figures below them (849, 529, 428, 339, 322, 312, 236, 80) carry false precision.
- **`ratchets bump` requiring commit-message justification** — the flag exists; the enforcement claim does not check out. Assume convention.
- **The beads details that a recommendation would copy verbatim** — the `<!-- bd-doctor-divergence: ok -->` waiver marker, the `Agent-Signature:` trailer with `unknown-model` fallback, the exact `--claim` atomicity, and the `bd-a3f8.1.1` hierarchical ids — are quoted at a specificity I could not confirm. The `discovered-from` edge, `bd doctor`, `.beads/issues.jsonl` and id-keyed import *do* check out.
- **adrs-core "0.12.1, 2026-09-03"** does not reconcile with the crate registry (`adrs` at 0.6.2). Single-maintainer crate either way.
- **Backlog.md's `back-355` evidence-mapping block** is n=1 — and is precisely the artifact type that is trivially fabricated after the fact, which is the thing this report recommends replacing.
- **Vendor implementation details at high precision** (Claude Code compaction internals: five files, 5,000-token thresholds, 25,000-token skill cap, Stop hook overridden after 8 blocks; Copilot's "59 minutes… cannot be extended") change between releases and are a bad foundation for a durable repo convention. The structural lessons survive; the numbers should not be cited.
- **Adoption counts** ("25+ agents", "26+ platforms", "88 AGENTS.md files in the main OpenAI repo") are marketing-shaped and say nothing about whether the reading agents honour nearest-file-wins the same way — which is the property the recommendation would rely on.
- **"Windsurf/Devin"** conflates two products with different rule systems and owners; the 6,000/12,000-char caps are Windsurf's.
- Two corpus items are labelled `[proposed]` with the completeness critic noting "no named source at all" while the sweep itself cites arXiv 2606.09498 — an internal inconsistency suggesting at least one may be researcher synthesis rather than a finding.

**Method caveat.** This report synthesises a research corpus supplied to me; **I did not independently fetch or verify a single URL in it.** Everything above marked well-evidenced is well-evidenced *within that corpus*. The arXiv identifiers cluster heavily in 2026 and several are single-author preprints with self-reported benchmarks (ContextCov 2603.00822 in particular claims 88.3% vs 67.0% constraint compliance with no replication, and its stated submission date does not match its identifier month). Treat every 26xx.xxxxx identifier as requiring a check before it is cited in a MiOS ADR.

**Primary sources the corpus actually reads from, by cluster:** agents.md; code.claude.com/docs (memory, hooks, skills, best-practices, plugins-reference, large-codebases, context-window); agentskills.io/specification; docs.github.com (repository instructions, issue forms, status checks, coding agent); cursor.com/docs/context/rules; docs.devin.ai; MrLesk/Backlog.md (README, AGENTS.md, `src/guidelines/agent-guidelines.md`, `backlog/tasks/back-355…md`); steveyegge/beads (README, AGENTS.md, AGENT_INSTRUCTIONS.md); github/spec-kit (`templates/commands/analyze.md`, `templates/tasks-template.md`, `templates/checklist-template.md`); Fission-AI/OpenSpec docs; eyaltoledano/claude-task-master `docs/task-structure.md`; GothenburgBitFactory/taskwarrior `doc/devel/rfcs/task.md`; cognitect.com Nygard ADR post; adr/madr `template/adr-template.md`; npryce/adr-tools; kubernetes/enhancements (`keps/README.md`, `kep.yaml`, `docs/kepval.md`); rust-lang/rfcs; go.googlesource.com/proposal; peps.python.org/pep-0001; rfc-editor.org/rfc/rfc7322; postgres `src/backend/access/nbtree/README`; ozimmer.ch (Y-statements); archunit.org userguide; import-linter and dependency-cruiser docs; semgrep.dev contributing; checkov.io; rust-clippy `adding_lints.md`; eslint.org RuleTester; conftest.dev; openpolicyagent.org annotations; ossf/scorecard `checks/write.md`; phpstan.org baseline; pre-commit.com; docs.pytest.org exit codes; insta.rs; slsa.dev (provenance v1.1, VSA v1.1, source-requirements v1.2); in-toto/attestation `spec/predicates/{test-result,svr}.md`; witness.dev; docs.chainloop.dev; docs.sigstore.dev; cli.github.com `gh_attestation_verify`; OASIS SARIF 2.1.0; NIST OSCAL assessment-results; ossf/gemara layer4; bazel.build/remote/caching; git-scm.com/docs/git-notes; flyway/liquibase checksum docs; terraform-docs output config; github-linguist overrides; docs.openstack.org/infra/storyboard/migration; spring.io Jira→GitHub migration post; developer.chrome.com Monorail migration; martinfowler.com StranglerFigApplication; hacks.mozilla.org BugBug; anthropic.com/engineering (effective-harnesses-for-long-running-agents, effective-context-engineering); trychroma.com/research/context-rot; aider.chat/docs/repomap; humanlayer ace-fca.md; ghuntley how-to-ralph-wiggum; docs.openhands.dev; imbue-ai/ratchets; keepachangelog.com; conventionalcommits.org.
