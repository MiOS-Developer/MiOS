<!-- AI-hint: ADR-0026 makes one strict-schema JSONL file the canonical record of every MiOS task (T-, AGY-, MON-, F-, M-, CODE-, G- ids), merged losslessly from every task list with verbatim provenance; the pure task lists it absorbed are deleted and can be rebuilt byte-for-byte with `mios-task source`. -->
<!-- AI-related: /usr/share/doc/mios/adr/README.md, /usr/lib/mios/schemas/task-record.schema.json, TASKS.jsonl, /usr/share/mios/mios.toml [tasks], tools/native/mios-task, ROADMAP.md, tools/check-tasks.py -->
---
adr: 0026
title: One canonical task store, merged losslessly from every task list
status: proposed
date: 2026-09-26
deciders: [operator, ai-pair]
tags: [tasks, backlog, queue, ssot, openai-strict-schema, lossless, generated-views]
laws: [1, 2, 8, 14, 16]
ssot_keys: [tasks, legibility]
related_ws: [WS-DEBT, WS-PROCESS]
supersedes: []
superseded_by: [0028]
---

# ADR-0026: One canonical task store, merged losslessly from every task list

## Status

Superseded by ADR-0028 (task_3462): `tasks.jsonl` at the repo root is now the only canonical task list, edited
directly; the store below and `.devloop/tasks.jsonl` are retired and live on as frozen provenance.

The id model below is retired too. ADR-0028's opaque-identity amendment gave every task an opaque
`task_<ULID>` and a creation-order number, and purged the prefixed ids this record describes; every task id in
this file was rewritten to its typed form, `task_<number>`. Git history keeps the record as first written.

proposed — 2026-09-26. Implements task_3354 (QUEUE-01) and the storage half of task_966 (QUEUE-02). The four
choices below were made by the operator in the question UI on 2026-09-26; the rest is the design that follows
from them. An agent never flips this record to accepted.

## Context

MiOS work is tracked in eleven lists across four repositories, and no machine can select the next task from
them without reading all of them (task_3354). Census, read 2026-09-26 at MiOS `a67750d`:

| List | Records | Id space |
|---|---|---|
| `TASKS.md` summary table (lines 7-1088) | 1080 rows, 1079 ids | task_1..task_1025 |
| `TASKS.md` sections (from line 1124) | 1025 headings, 1024 ids | T- |
| `AGY-TASKS.md` | 2140 headings: 2098 ids + 42 range banners | task_1026..task_3165 |
| `.devloop/tasks.jsonl` | 35 | task_3370..task_3404, task_3396..task_1021 |
| `usr/share/mios/agents/TASKS.md` | 24 | task_3166..task_3183 |
| `ROADMAP.md` | 115 `CODE-NN` items under 39 `WS-*` | CODE- |
| `usr/share/mios/docs/MIOS-GEMINI-TASKS-*.md` | 8 | G- |
| mios-micro `ROADMAP.md` | 9 | task_3308..task_3316 |
| -dev-loop `.devloop/tasks.jsonl` | 1641 | T-, AGY- (an import of the two lists above) |
| -dev-loop `.devloop/backlog_archive.jsonl` | 1537 | T-, AGY- (archived import) |
| -dev-loop `.devloop/HISTORICAL_BACKLOG.md` | 1537 | the same ids as the archive |

Defects the census proved:

1. **Two id meanings.** task_1018..task_1021 are KEYMAP-01/DESKTOP-02/ACTIONS-01/BRIDGE-02 in `TASKS.md`
   (lines 1081-1084) and the model-OCI tasks in `.devloop/tasks.jsonl` and mios-micro. The -dev-loop import
   also merged that repo's own task_1..task_5 into MiOS task_1..task_5, so two MiOS bodies were lost and three
   were grafted onto unrelated tasks.
2. **Lossy structure in the existing import.** 3173 of 3178 imported negative controls and 2229 of 3178
   positive controls are placeholders. `Who` was dropped on all 885 records that carry it, 70 AGY tasks marked
   `**DONE**` were imported as open, 33 range banners were dropped, and the import's own note claims
   "3,204 ported, 0 dropped" where 3178 exist.
3. **The table and the sections disagree.** 55 ids exist only as table rows; task_31 has two rows and two
   sections with different titles; the header claims 264 tasks.
4. **Status lives in prose.** AGY status is a heading decoration (`**[DONE]**` 916, `**DONE**` 63, bare
   `DONE` 7, `[BROKEN]` 9); T status is free text in 12 spellings.
5. `check_tasks_status_parity` compares table cells to section status only; nothing compares T- to AGY-,
   so task_2288's done-when is not met.

## Decision

### Operator decisions (2026-09-26, question UI)

- **Location:** MiOS owns the canonical store for every id space. The sibling repositories' lists become
  projections of it.
- **Scope:** all eleven lists above, including the F-, M-, G- and `CODE-NN` lists.
- **Form:** the OpenAI pattern, from current upstream sources: one JSON object per line, a caller-owned unique
  join key, and a strict `json_schema` (`strict: true`) under the same rules as every other MiOS schema.
- **Conflicts:** precedence plus keep-everything. Typed fields come from the highest-precedence source; every
  other occurrence is kept verbatim and every disagreement is listed. Nothing is picked silently.
- **One master list:** a single file at the repository root, `TASKS.jsonl`, holds every global task. The pure
  task lists it absorbed (`TASKS.md`, `AGY-TASKS.md`, `usr/share/mios/agents/TASKS.md`, the two G- documents)
  are deleted outright; no stub or redirect stays behind. `ROADMAP.md` and `.devloop/tasks.jsonl` remain.

### What is built

1. **Store:** `TASKS.jsonl` at the repository root, one file, whitelisted in `.gitignore`. One record
   per line, valid against `usr/lib/mios/schemas/task-record.schema.json` (`mios_task_record`, strict). The
   store is declared in `mios.toml [tasks.store]` (Law 8): path, schema name and version, the ordered source
   list and its precedence.
2. **Record:** typed core fields (`title`, `status`, `priority`, `size`, `workstream`, `domain`, `owner`,
   `goal`, `what_how`, `where`, `why`, `do_not`, `done_when[]`, `verify[]`, typed `deps[]`), an `extra[]`
   passthrough of `{key, value_json}` pairs for every field of the defining occurrence that the schema does
   not name (every other occurrence is an import whose full text `sources[]` already keeps), `conflicts[]`, and
   `sources[]` — every verbatim occurrence of the task with `source_file`, `byte_offset`, `byte_length`,
   `kind`, `sha256` and the whole list's `source_bytes`/`source_sha256`. A list's non-task text (headers,
   banners, tables' framing) rides as a `passthrough` slice on the record before it, so the slices of every
   list tile it exactly and the one file proves itself. A strict schema cannot hold an open map (`additionalProperties` must be `false`), so the
   passthrough is a pair list, not an object.
3. **Ids:** kept exactly as written; never reminted. `key` is the unique join key and equals `id`, except where
   two origins use one id for two different tasks (defect 1). There `key` is `<origin>#<id>` for the
   lower-precedence origin, `id` stays byte-identical, and each record names the other in `deps` as
   `references`. Readers join by `key`, never by line order.
4. **Status:** normalised to the OpenAI words — `pending`, `in_progress`, `completed` (plan steps),
   `incomplete` (an item that stopped short: blocked, broken, deferred), `cancelled` (retired, superseded).
   The source's own words stay in `status_raw`, so the normalisation loses nothing.
5. **Dependencies:** typed edges. Only `blocks` gates readiness; `related`, `converted_to`, `converted_from`
   (the 649 `**Converted:** AGY-N` links) and `references` never do.
6. **Tool:** `tools/native/mios-task` (Law 14: a Rust static binary) with `migrate`, `check-lossless`,
   `validate`, `ready --json` and `source <list>`. `ready` reads typed fields only. `source` prints an
   absorbed list byte-for-byte as it was, rebuilt from its slices.
7. **Absorbed lists:** a deleted list is declared `absorbed = true` in `[tasks.store].sources`. `migrate`
   rebuilds its bytes from the store instead of reading a file, and `check-lossless` fails `REAPPEARED` if the
   file exists again, so the master list cannot grow a second copy or a redirect. The task gates in
   `tools/check-tasks.py` keep their logic and read each absorbed list through the same rebuild.
8. **Writes (phase 2, separate change):** `mios-task add/set` becomes the only way to change a task.

### Parsing

Every list is parsed once and split into records and passthrough slices that tile the file exactly. A
fenced code block hides the headings inside it only in `HISTORICAL_BACKLOG.md` (`fences = true`), whose notes
wrap old task bodies in fences; there a `## [FOLDED]` heading always starts a record, because an embedded
body can leave a fence unbalanced. In the hand-written lists a task heading always starts a record: a stray
closing fence at `AGY-TASKS.md:10241` renders lines 10241-11686 as one code block today, which hides 145
tasks from any Markdown reader but not from the store.

### Precedence (highest first)

`TASKS.md` sections, `AGY-TASKS.md`, `usr/share/mios/agents/TASKS.md`, `ROADMAP.md`, the G- documents,
mios-micro `ROADMAP.md`, `TASKS.md` table rows, `.devloop/tasks.jsonl`, -dev-loop `.devloop/tasks.jsonl`,
-dev-loop `backlog_archive.jsonl`, -dev-loop `HISTORICAL_BACKLOG.md`. The hand-written lists outrank the
imports because the census found the imports older than the lists they copied.

## Rationale

- **One JSONL file, not one file per task.** task_966 proposed one Markdown file per task. That shape adds about
  3,200 tracked files; `[legibility].max_tracked_files` is 3409 and the tree already tracks 3427, and the
  ratchet only falls. One file adds one path. Per-task Markdown remains available as a rendered view.
- **Why the OpenAI pattern.** MiOS law already requires OpenAI-format schemas everywhere, with the in-tree
  strict converter (`usr/lib/mios/agent-pipe/mios_mcp_schema.py` `make_schema_strict`) as the shape to
  standardise on. The store's schema is a fixpoint of that converter. JSONL with a caller-owned unique id and
  join-by-id is the batch-file convention; the status words are the plan-step and response-item vocabulary.
- **Why verbatim provenance.** Parsers for eleven hand-written formats will misread some field. When every
  byte of every source is kept with its offset and hash, a misparse costs a typed field, never data, and the
  `lossless` check proves it on every run.
- **Rejected:** a database as the canonical store (the gate would need a live database); two-way sync with
  hand-edited lists (two sources of truth); renumbering colliding ids (the operator's rule: never reminted).

## Consequences

Done when:

1. `mios-task check lossless` exits 0: for every declared source, the `sources[]` slices of all records plus
   the recorded passthrough cover every byte, and every slice's sha256 matches. A copy of the store with one
   slice shortened fails, naming the task key and source file.
2. `mios-task validate` exits 0: every line is valid against `mios_task_record`, every `key` is unique, every
   `blocks` edge resolves. A planted duplicate key fails, naming it.
3. `mios-task check views` exits 0: every generated view equals a fresh render. A hand edit to a view fails,
   naming the file and the task.
4. `mios-task ready --json` lists the pending tasks whose `blocks` edges are all completed, reading typed
   fields only.
5. The drift gate runs 1-3; `tests/drift-gate-negatives.sh` plants each failure.

Costs:

- The store holds the verbatim text of every list, about 27 MB; `[legibility].max_tracked_mb` is regenerated
  from 209 to 235 by `mios-size-ceiling` (operator-approved).
- `TASKS.md` and `AGY-TASKS.md` no longer exist and their records are frozen history. Until `mios-task
  add/set` lands, a task is added or changed through a live source (`.devloop/tasks.jsonl` or `ROADMAP.md`)
  followed by `mios-task migrate`; editing `TASKS.jsonl` by hand breaks its slice hashes and fails the gate.
- The sibling repositories' lists become generated there only when their owners regenerate them.
- The typed-field parsers are best-effort by design; `extra[]` and `sources[]` carry whatever they miss.
- The store quotes history verbatim, so scans that read the tree as live configuration skip it exactly as
  they already skip `TASKS.md` and `AGY-TASKS.md`: the unbound-port register (`tools/check-ssot.py`) and the
  schema-consumer register (`tools/check-testhygiene.py`). At the root it is also outside the port-fallback
  sweep, which only walks `automation/`, `usr/`, `etc/` and `tools/`; under `usr/` that sweep rewrote a quoted
  `${MIOS_PORT_*:-N}` inside task_1630's history and broke its slice hash.
- `ROADMAP.md` and `.devloop/tasks.jsonl` stay live sources: `check_task_store` fails `STALE` when either
  changes after the last `mios-task migrate`.
