<!-- AI-hint: MiOS -- dev-loop finding task_945 (GATE-05): the recorded verdict, evidence and citations from re-measuring that gate's two claims.
     AI-related: automation/98-drift-checks.sh, tools/drift-checks.py, .devloop/LEDGER.md -->
# Finding: task_945 (GATE-05)

## VERDICT
CONFIRMED

## WHAT IS ACTUALLY TRUE
The `check_no_inert_ssot_tables` gate makes two errors: it credits tables from prose, and it conflates sub-tables with top-level tables.

1. **Locate the check**: The predicate is located at tools/drift-checks.py:441 (`in_ssot_context`), specifically the regex `re.compile(r'\[["\']' + t + r'["\']\]')`.
2. **Credits a table from prose**: The check credits the `[offline]` table based on the code `if p["offline"]:` in usr/lib/mios/agent-pipe/server.py:825. This `p` is a local Python dictionary having nothing to do with SSOT, but because the file mentions `"mios.toml"` elsewhere, the check considers `["offline"]` to be an access to the SSOT table.
3. **Cannot tell a sub-table from a top-level one**: The parser extracts only top-level dictionary keys as tables (tools/drift-checks.py:452). For example, `[security]` and its sub-table `[security.privileged_units]` are both credited if `"security"` is accessed, because the check does not know `security.privileged_units` exists as a separate table.
4. **Constructed Violation**: Adding a genuinely inert top-level table `[devloop_test_inert]` to `usr/share/mios/mios.toml:1` without any consumer fails the check (it does not stay green). The check correctly reports: `SSOT table [devloop_test_inert] has no access-shaped consumer`.

## NUMBERS
- **Currently Credited Tables**: 153
- **Tables Credited Under a Read-Only Predicate**: 116

## PROPOSED FIX
Update `tools/drift-checks.py` to recursively collect all table paths (e.g. `security.privileged_units`) and remove the loose prose-matching strings in `in_ssot_context`.

## FILES TO CHANGE
- tools/drift-checks.py

## NEGATIVE CONTROL
N/A

## UNVERIFIED
None

## BLAST RADIUS (measured 2026-09-19)
The proposed fix has two halves, and both are operator-timing decisions rather
than drop-in repairs. Measured by probe, then reverted:

- Dropping the loose `in_ssot_context` fallback takes the check from **1**
  unconsumed table (`[gpu]`) to **38**. The 37 newly-surfaced tables are either
  genuinely dead SSOT surface or need registering in `[ssot_tables].unconsumed`
  with reasons.
- Recursing into sub-tables raises the checked-name count from **157** top-level
  tables to **1,461** (1,304 nested). How many of those would be unconsumed is
  unmeasured.

`[ssot_tables].max_unconsumed` is shrink-only, so neither half can be armed by
raising it. This is the same shape as task_944's note: arming the gate makes the
pipeline redder, and the operator chooses the timing.

The PROPOSED FIX above records `NEGATIVE CONTROL: N/A`. That is a gap, not a
property of the work: a change to a credit-granting predicate needs a planted
table that must be reported inert, and a genuinely consumed one that must not.

## BLOCKER: the fix cannot land as Python (measured 2026-09-19)
A working fix was built and verified, then reverted unlanded. It is preserved as a
patch, not committed, because it cannot pass the repo's own gates:

- The predicate becomes: a `["table"]` subscript counts only when the thing
  subscripted IS the parsed SSOT -- the parse on the same line, or a name bound
  from one, closed transitively so helpers like `_ssot_root()` still count.
- Measured result: unconsumed goes 1 -> 6 (artifacts, gpu, kargs, pipeline, power,
  preflight), not the 37 a blunt removal produces. `[cockpit]` and `[database]` are
  correctly KEPT -- an earlier draft lost them, which is the same defect mirrored.
- A fifth arm was added to `test_no_inert_ssot_tables` planting the exact false-credit
  shape. It passes against the fix and fails against the current checker.
- Cost, after compression: +31 lines in `tools/drift-checks.py`.

`[legibility].max_tooling_python_lines` sits at its exact floor, and
`check_ratchet_direction` rejects the raise. The only exemption mechanism,
`[drift.generated_ceilings]`, is for MECHANICALLY DERIVED budgets; using it for a
hand-set Law 14 ceiling would be a suppression dressed as an exemption.

So the repo has already decided this: Law 14 says the check converts to Rust. Landing
task_945 means porting `check_no_inert_ssot_tables` to `tools/native/`, not adding
Python. That is the real shape of this task, and it is larger than a predicate fix.
