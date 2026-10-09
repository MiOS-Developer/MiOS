<!-- AI-hint: ADR-0028 makes tasks.jsonl at the repo root the only canonical MiOS task list, edited directly; TASKS.md is its rendered documentation plus an operator-overrides block every task tool applies; the retired stores live on as frozen, byte-verified provenance. Supersedes ADR-0026. -->
<!-- AI-related: /usr/share/doc/mios/adr/README.md, /usr/share/doc/mios/adr/0026-global-task-store.md, tasks.jsonl, TASKS.md, /usr/share/mios/mios.toml [tasks.store], /usr/lib/mios/schemas/task-record.schema.json, tools/native/mios-task, automation/98-drift-checks.sh -->
---
adr: 0028
title: One canonical task list, tasks.jsonl, with TASKS.md as its rendered view and operator overrides
status: proposed
date: 2026-10-03
deciders: [operator, ai-pair]
tags: [tasks, backlog, ssot, openai-strict-schema, generated-views, overrides, dev-loop]
laws: [1, 7, 8, 14, 16]
ssot_keys: [tasks.store]
related_ws: [WS-CI, WS-PROCESS]
supersedes: [0026]
superseded_by: []
---

# ADR-0028: One canonical task list, tasks.jsonl, with TASKS.md as its rendered view and operator overrides

## Status

proposed — 2026-10-03. Implements T-1169. The operator's directives, verbatim: "tasks.jsonl is the ONLY
canonical tasks list"; "tasks.md is just documentation(s) and where operator overrides goes". The operator
chose the root path and the MiOS-related-only scope. An agent never flips this record to accepted.

## Context

ADR-0026 merged eleven lists into a generated store, `TASKS.jsonl`, that nobody edited: a task changed in a
live source (`.devloop/tasks.jsonl`, `ROADMAP.md`) and `mios-task migrate` re-merged everything, reading
`-dev-loop`'s backlog and `mios-micro`'s roadmap from sibling checkouts. So MiOS had two task files an agent
could reach for, a third (the dev-loop toolkit's) it depended on, and a store whose check went red whenever
`ROADMAP.md` was regenerated. The dev-loop toolkit now finds a project's task file at `<root>/tasks.jsonl`,
keeps its status dialect, and applies a `TASKS.md` overrides block (JSON lines between
`<!-- overrides:begin -->` and `<!-- overrides:end -->`, under a `# TASKS` banner) on top of it.

## Decision

1. **One list.** `tasks.jsonl` at the repo root is the only canonical MiOS task list. Its path, schema and
   documentation file are declared once, in `mios.toml [tasks.store]`. It is edited directly: by
   `mios-task add/set/claim/release` or by the dev-loop task tools, which resolve the same file. Every line is
   a `mios_task_record` (strict OpenAI `json_schema`), serialized like Python
   `json.dumps(obj, ensure_ascii=False)` with keys in the schema's `required` order, so the toolkit and
   `mios-task` rewrite a record to the same bytes. Statuses are the OpenAI plan words. Lines end in LF only:
   `check` and `fmt --check` name a line carrying a carriage return, and `set`/`claim` refuse to rewrite the
   file around one (`fmt` is the repair).
2. **TASKS.md is generated.** `mios-task render` writes it from `tasks.jsonl`: `# TASKS`, the overrides
   block, a status summary and one line per task grouped by workstream and epic. No dates, so the render is
   byte-deterministic. Outside the overrides block it is never hand-edited; `mios-task check` fails on any
   difference and names the line.
3. **Operator overrides.** Each line of the block that starts with `{` is `{"id": ..., field: value, ...}`.
   Every task reader (`check`, `render`, `ready/next`, `claim`, and the toolkit's read-only ops) applies the
   fields on top of the record; the last line wins per (id, field). An unknown id, a field the record does
   not have, `id`/`provenance`, or a value the schema refuses fails `check`, naming the TASKS.md line.
   `mios-task overrides fold <id>` moves an override into `tasks.jsonl`.
4. **Retired stores.** `TASKS.jsonl`, `.devloop/tasks.jsonl`, and the lists ADR-0026 absorbed are listed in
   `[tasks.store].retired` and must not exist; a file named like the canonical list (any case) at the root or
   one directory down is a second store. Both fail `check`.
5. **Frozen history before the opaque-ID migration.** Every record migrated from a retired list keeps, in
   `provenance.sources`, the verbatim slices it owned (file, kind, offset, length, sha256, and `after`: the
   id of the record owning the slice before it). Concatenated by offset, the slices rebuild each list in
   `[tasks.store].frozen` to the recorded digest (`mios-task source <list>`), so the task gates over the old
   `TASKS.md` and `AGY-TASKS.md` keep running on frozen history. A dropped record leaves a gap whose next
   slice names it; an edited slice fails its digest; `[tasks.store].migrated` counts the carried records,
   each of which must own at least one slice. `[tasks.store].migrated_sha256` freezes their identity -- every
   migrated record's provenance key, id, aliases and slice set -- so a swap that keeps the count (a record whose
   only slices lie outside the frozen lists, such as AGY-1692, dropped and another appended), a rewritten key, a
   lost alias or a stripped slice fails `check`. A `#n` id must name the id it was renamed from in its aliases.
6. **Former ids before the opaque-ID migration.** A migrated record that was renamed keeps its old id in `provenance.aliases`. Where that old
   id is also a live task (T-031, T-1104..T-1107 against T-031#2, T-1104#2..T-1107#2), `mios-task set/claim/
   release` refuse it as ambiguous unless `--exact` is passed; an id that is only a former id resolves to the
   renamed record. `check` prints a note per such id, and refuses a record born in the list that takes one.
   **Follow-up (dev-loop toolkit):** its `tasks set ID` matches `id` only, so `tasks set T-1104` edits task
   T-1104, never the lane's former T-1104 (now T-1104#2); the toolkit should refuse an id that is also a former
   id of another record, as `mios-task` does. Until it does, the `check` notes name each such id.
7. **Scope.** MiOS reads no `-dev-loop` or `mios-micro` file. The operator's classification decisions put
   7 records in the toolkit (they stay in `-dev-loop`) and keep the rest. A MiOS record that only the toolkit
   backlog held (AGY-1692) keeps its toolkit bytes as provenance.

### Opaque identity amendment (operator-directed, 2026-10-09)

This amendment supersedes points 5 and 6 for a list after `mios-task migrate-ids`; those points
continue to describe the first migration and the compatibility checks on a list not yet migrated.
It does not authorize changing Git history or silently accepting new frozen digests.

- Every record has `object = "task"`, an opaque `id` spelled `task_<ULID>` (26 Crockford characters),
  and a unique positive integer `number`. Operators and rendered documents use `task_<number>`;
  structured dependencies and related edges hold the opaque id. Both spellings resolve to one record.
  The schema owns the object word. Former ids, provenance keys and aliases cease to be lookup identities.
- The one-shot migration numbers records by creation date and existing list order within each date;
  undated records come first. It derives opaque ids deterministically from the old identity and that
  ordering. New tasks receive a fresh opaque id and a number greater than any already issued.
  `[tasks.store].numbered` is the non-reissue floor; the identity digest freezes migrated numbers,
  opaque ids and owned slices. Dropped records, swapped identities, changed slice sets, duplicate
  numbers and numbers out of creation order fail `check`.
- The authorized token rewrite includes frozen slice text, dependencies, overrides, documentation,
  tracked textual files and filenames when `--rewrite-tree` is requested. It updates slice offsets,
  lengths, hashes, predecessor labels, frozen digests and the migrated identity digest together.
  The new frozen baseline is explicitly the token-rewritten corpus, not the original byte digest.
  Git history retains the original bytes. Non-task lookalikes such as aircraft names and encodings
  remain untouched; an unresolved task token requires an explicit `--map OLD=OLD2` decision.
- Planning and rename-collision checks happen before source writes. `--dry-run` preserves source;
  `--report` is an explicitly requested planning output and may be written during a dry run.
  Migration inputs cannot be used as the report path. Publication stages byte/mode backups and
  replaces individual files atomically; an ordinary write or final validation failure restores
  touched files, renames and newly created directories. Incomplete restoration retains backups and
  reports their location. This is recovery for reported failures, not an atomicity claim for a host crash.
- `[tasks.store].purged` records the retired token shapes. `mios-task check --tree` rejects them in
  tracked paths and text. An already migrated list is validated before `migrate-ids` reports a no-op.
  Rehearse the migration in an isolated worktree, inspect its diff and run the gates before integrating
  a repository-wide rewrite. Positive, planted-negative, idempotence and no-write controls cover the tool.

How the rewrite decides a token, and what it keeps:

- **The opaque id.** The ULID's 48-bit time is the record's `created` day at 00:00 UTC plus its rank among
  the records of that day (an undated record counts from 1970-01-01), so ids sort the way numbers do. Its 80
  random bits are the first 80 bits of SHA-256 over the old id, so the migration is reproducible from git
  history byte for byte. `mios-task add` takes the current millisecond (or one past the newest id) and
  hashed randomness, and the next number above both the list's highest and `[tasks.store].numbered`.
- **A token.** It follows the old id grammar, starts after a non-word character and ends before one; the
  longest candidate wins, so `X..Y` range ids and `X#2` renames are one token. It is rewritten when it is a
  task's id; a former id (`provenance.aliases`) inside its own task's text names that task, elsewhere the
  live task of the same id; a task whose title opens with it (`NAME: ...`) answers to it; a member of a
  range a banner task covers names the banner; a bare range end (`X-5..9`) is rewritten with its start.
  Anything else of an old shape refuses the whole migration until the text is fixed or `--map` decides it.
- **Kept on purpose.** An id that is also a workstream name (`WS-...`, the 14 workstream records of the
  hardening campaign) names the workstream in text and stays; the record's own id is purged and the name
  moves into its `workstream`. Look-alikes that are not old ids (aircraft, encodings, CVEs, port and line
  numbers) are untouched. Test fixtures of the two one-shot migrators model the legacy shape with prefixes
  no real task ever had, so neither the rewrite nor the gate touches them.
- **What is gone.** Every old id in `tasks.jsonl` (records, frozen slices, `after` labels), `provenance.key`
  and `provenance.aliases`, the overrides block, every tracked file and path. Commit messages and the rest
  of git history keep the old ids; that history is immutable and the operator accepted it.
- **Beyond the list.** `--mapping-out FILE` writes every token the migration resolves, with its typed id,
  outside the tree; `mios-task rewrite-ids --mapping FILE --root DIR` applies the same rewrite to a sibling
  repository with no task list of its own, so the Law 15 mirrors in mios-bootstrap match again. The comment
  corpus carries a landing by the comment's hash, which the rewrite changes: `mios-manual rehash` moves each
  rewritten comment's ledger rows and `mios-src` doc markers to its new hash, so `distill` does not land the
  same prose twice.
- **The frozen-list gates.** `tools/check-tasks.py` reads the rewritten frozen lists: TASKS.md rows and
  sections by `task_<number>`; AGY-TASKS.md headings by `task_<number>` (a range banner is one task now).
  `[tasks].schema_from` names the first task the schema is demanded of and `schema_also` the ones created
  after it but filed before it, so the demanded set is exactly the one the AGY number floor described.
  Every task a Dep line or the TASKS.md text names must be a task in `tasks.jsonl`.

### The migration (one shot, `mios-task migrate-canonical`)

Counted both ways from `TASKS.jsonl` (3491 records) and `.devloop/tasks.jsonl` (115 lines):
store 3491 = kept 3484 + toolkit 7 + devloop-only 0; lane 115 = merged into their store record 115 + lane-only
0; out 3484 lines. Every kept record keeps its id (a second task that reused an id keeps the `#n` form the
store already keyed it by: T-031#2, T-1104#2..T-1107#2) and its status (0 changed: a store record keeps the
store's word, a lane record its lane word). Lane claims are carried as `owner` (lane-b, Codex, claude-code);
the store's free-form "Who" text moves to `provenance.owner_raw`. Edges, counted from the lane file: a lane
record's `depends_on` stays a block; its `archived_dependencies` -- edges the toolkit took out of `depends_on`
because the target was archived -- become `related` entries of type `archived` (3: T-1070 -> T-1050,
T-1071 -> T-1055, T-1073 -> T-1046), never blocks, and `check` fails if one returns to `depends_on`. The 61
`depends_on` edges that formed cycles move to `related`, each listed in the report, because a cycle cannot be
scheduled; no edge dangles and no lane edge was renamed. Every frozen list
rebuilds byte for byte to the digest the retired store recorded.

## Rationale

- **Edit the list itself.** A generated store that nobody edits needs a second, editable source, which is the
  double-tracking the operator removed. Records with canonical bytes make direct edits safe for both tools.
- **Overrides in the toolkit's format.** One override syntax means the dev-loop tools and `mios-task` see the
  same effective list; a MiOS-only format would make the toolkit ignore the operator.
- **Frozen slices over a separate archive file.** A second file holding history would be a second store to
  keep in step. The slices travel with their records, so dropping a record is detectable and nameable.
- **Rejected:** re-deriving statuses from free prose during the migration (it would change history the
  operator never reviewed); keeping `TASKS.jsonl` as a projection (two files, one stale).

## Consequences

Done when:

1. `mios-task check` exits 0 on the tree: schema, unique ids, resolvable `depends_on`/`epic`, no cycles,
   evidence on every completed record, canonical serialization (LF only), no retired or second store, the
   frozen history rebuilds, the migrated count and identity digest hold, no archived edge is a block, overrides
   validate, and `TASKS.md` equals its render.
2. `tests/drift-gate-negatives.sh test_task_store` plants a duplicate id, a hand-edited `TASKS.md` task line,
   a dropped record, a retired `.devloop/tasks.jsonl`, an edited frozen slice, an archived edge back in
   `depends_on`, a count-keeping swap of AGY-1692, T-1104#2 without its alias, a rewritten provenance key and a
   CRLF line; each fails naming the plant, and a copy of the task files with no sibling checkout beside it passes.
3. The dev-loop toolkit resolves `<root>/tasks.jsonl`, validates it, and reads the overrides block.

Costs:

- `tasks.jsonl` holds the retired lists' text, about 14 MB, smaller than the 28 MB store it replaces.
- The dev-loop toolkit's own `tasks render` also writes `TASKS.md` (in its format, with a date); after it,
  `check_task_store` fails until `mios-task render` runs. Both keep the overrides block verbatim.
- A record the toolkit adds lacks MiOS's extra fields; `mios-task fmt` fills them, and `check` says so.
- `ROADMAP.md` and mios-micro's `ROADMAP.md` stay documents. Their task items were migrated; their later
  edits no longer touch the task list.
