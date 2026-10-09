<!-- AI-hint: The standing operating agreement between the operator and the agents working on MiOS -- what is decided, what is delegated, how autonomy is bounded, and the ground truth an agent cannot read from the tree. Read at session start. -->
<!-- AI-related: AGENTS.md, CLAUDE.md, TASKS.md, ROADMAP.md, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md -->
# Operating agreement

Captured from two structured question rounds with the operator. Everything here
is an operator decision, not an agent inference. Where a decision contradicts
something written in the tree, **this file is right and the tree is stale** --
and fixing the tree is itself a task.

## 1. Ground truth an agent cannot read from the source

| Claim | Reality |
|---|---|
| ROADMAP says bare metal is **untried** | **Wrong.** It has booted on bare metal and in the VM. |
| The Blade fleet | **One Blade exists.** The 2-6 fleet does not. Mesh, quorum and fleet behaviour are unobserved. |
| `ghcr.io/mios-dev/mios` | **Published.** Merge to main -> CI builds -> pushes to ghcr. |
| The drift-gate | **Gates releases.** Main red means nothing ships. |
| The AI plane | Containers start; **nothing real uses them**. |
| pgvector contents | **Unknown** -- must be checked on the Blade before any schema change. |
| `bootc rollback` | **Never exercised.** The recovery story is unproven. |
| Other agents | **None active.** `AGY-TASKS.md` is historical. |

## 2. What "done" means

All four at once, not a choice between them:

1. A stranger pastes one line and gets a working machine.
2. The fleet: 2-6 Blades, mesh, self-replicating.
3. A self-hosting agent OS -- MiOS develops MiOS.
4. A personal sovereign workstation that simply works.

**Self-replication** means all three scales as one loop: the OS rebuilds its own
image from its own source, a Blade provisions its peers, and the agent plane
does the development work itself.

## 3. Standing directives

**Autonomy.** Loop until the task list is empty or a genuine fork appears. A
fork gets an RFC, not a guess. No per-commit approval.

**Budget.** No meaningful limit. Thoroughness wins -- prefer full verification,
adversarial checks and research workflows over economising.

**Scope in this phase.** Mid-early development and research: nothing is
off-limits, including the boot path and the published image identity.

**Deletion.** Propose, never delete, without a yes. The exception is a proven
like-for-like replacement landing in the same commit as its successor, which
ADR-0021 already requires.

**Yak-shaving.** A defect found mid-task gets fixed -- separate commit -- then
the original task continues. Root cause over workaround, even when it grows the
branch.

**Commit messages.** Long and structured. In an agent-authored repository the
commit message is often the only place the reasoning survives, so it is a
first-class documentation surface, harvested like any other.

**Law changes.** Propose freely through an ADR; the operator ratifies. A law
that is unenforceable or measuring the wrong thing is amended through the same
door it came in (ADR-0007: ADR + `[laws]` row + drift-check).

**Both repositories.** `mios-bootstrap` is an equal partner, kept in lockstep
per Law 15 -- not a follower that changes only when forced.

## 4. Consolidation, globally

The recurring instruction across every axis: **collapse and categorise so that
both a person and an agent can audit by hand.**

- **Gates** -- 209 checks become one binary per category, many checks inside
  (ADR-0021's gate category). Several red checks are believed to be measuring
  the wrong thing; audit the predicate before satisfying it.
- **Tests** -- 337 files audited and consolidated into fewer, categorised ones.
- **Top-level directories** -- `field/`, `config/`, `images/`, `installation/`,
  `specs/` surveyed, collapsed into where they belong, categorised, refactored
  globally.
- **Backlogs** -- two files totalling ~40k lines become one machine-selectable
  queue. A finished task moves to a DONE archive keeping its ID and a one-line
  record; the live file stays small enough to select from.
- **Docs** -- lossless consolidation. Nothing is deleted until its content
  provably lands elsewhere, by the content-hash ledger the generative-docs spec
  already describes.

## 5. The documentation model

Documentation is **started by hand**, then each iteration **appends harvested
comment blocks per category section, verbatim, with the block's content hash**.
The hash is what lets the ledger prove a passage landed, which is what makes
deleting the source comment safe.

Docs are generative **and** mirrored as native Linux man pages. Three gaps are
open at once: `docs/design/` sits outside the pipeline entirely, coverage is
thin, and nothing gates the mirror against drift.

This also explains the narrative ratchet: 174 unmigrated blocks are a **harvest
queue**, not a scold. Compressing rationale out of code to hold the number is
the wrong response.

## 6. Decided approaches

| Question | Decision |
|---|---|
| Rust tier dispatch (task_962) | Per-stage absolute path, one commit each, byte-identical output per stage |
| The 112 unwired modules | Real features awaiting wiring. **Inventory first**, then wire; nothing culled |
| `[migration].use_rust_resolver_*` | They are **goals**, not state. Rename so they stop reading as "done" |
| Retired ports | **Zero occurrences, gated, no exceptions** -- including fixing the generators behind the two generated units |
| `docs/adr/` shadow namespace | Consolidate losslessly into the canonical namespace |
| `AGY-TASKS.md` | Condense and compact into a historical record |
| `server.py` (8,961 lines) | Decompose. Overdue, and cheapest now while nothing uses it |
| Verification | The operator runs bakes **and** a CI bake job is added |

## 7. Highest priority

**The Rust tier actually running.** task_962: the binaries that ship in the image
should be the ones that build it.
