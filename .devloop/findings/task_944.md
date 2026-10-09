<!-- AI-hint: MiOS -- dev-loop finding task_944 (GATE-04): the recorded verdict, evidence and citations from re-measuring whether that gate can be driven red.
     AI-related: automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, .devloop/LEDGER.md -->
# Finding: task_944 (GATE-04)

## VERDICT
REFUTED

## WHAT IS ACTUALLY TRUE
We reproduced the earlier verdict of REFUTED (and historically STALE), but overturned the prior run's unsubstantiated reasoning and fabricated numbers (the earlier finding claimed 7,106 in-scope files and 5,645 examined files in a repository containing only 3,316 git-tracked files).

TASKS.md row task_944 (at TASKS.md:10924) asserts that the Law 9 closure gate "exempts nearly the whole tree, so it has never failed and cannot" because it skips any path under `INTERNAL_PATHS` (TASKS.md:10925). Both clauses of this claim are refuted by direct measurement:

1. **Gate Call Site**: Law 9 (ONE-CANONICAL-NAME) is defined in CLAUDE.md:80 and registered in the SSOT laws table at usr/share/mios/mios.toml:2206. The gate logic is implemented in automation/lib/mios_var_closure.py:120 (`referenced_set`). It is invoked from automation/98-drift-checks.sh:1104 inside `check_var_closure()`, which is executed during drift checks at automation/98-drift-checks.sh:3751.
2. **Exemptions Do Not Cover the Tree**: The `INTERNAL_PATHS` exclusion described in TASKS.md:10925 was removed in commit e0622e4bc72 (task_989). The gate currently examines 1,158 consumer files across the repository, which is 65.20% of all consumer-scoped files matching `CONSUMER_GLOBS` (defined at automation/lib/mios_var_closure.py:42). The active exemptions are strictly targeted: test files (automation/lib/mios_var_closure.py:139), SSOT emitter files (automation/lib/mios_var_closure.py:22, automation/lib/mios_var_closure.py:143), and design documentation directories (automation/lib/mios_var_closure.py:135).
3. **The Gate Can and Does Fail**: The gate is actively enforced and goes red whenever a violation occurs. When an unemitted variable reference (e.g. `MIOS_TEST_UNEMITTED_XYZ123`) was planted in a consumer file under `automation/` or `usr/lib/mios/`, running `check_var_closure` immediately detected the unledgered name, output `VIOLATION: check_var_closure: var-closure: 'MIOS_TEST_UNEMITTED_XYZ123' is referenced but NOT emitted and is not on the ledger`, and exited with code 1. Furthermore, automated test coverage in tests/drift-gate-negatives.sh:1962 (`test_var_closure`) actively verifies failure upon probe planting (tests/drift-gate-negatives.sh:1985), ledger deletion, ghost entries, and missing ceiling.

## NUMBERS
Direct measurement of the repository at the current revision:

- **Total Non-Ignored Files in Tree**: 3,325 (3,316 git-tracked files).
- **Files in Gate Consumer Scope**: 1,776 files matching `CONSUMER_GLOBS` (`*.container`, `*.service`, `*.timer`, `*.py`, `*.sh`, `*.toml`, `*.ps1`, `*.psm1`, `*.yaml`, `*.yml`, `Justfile`, `.env.mios`, `*.tmpl`).
- **Files Actually Examined**: 1,158 files.
- **Files Exempted from Consumer Scope**: 618 files (34.80% of consumer scope).
  - *Exempted by Test Predicate* (automation/lib/mios_var_closure.py:139): 609 consumer-scoped files (629 files across the entire tree).
  - *Exempted by Emitter Predicate* (automation/lib/mios_var_closure.py:143): 9 consumer-scoped files (11 files listed in `EMITTER_SUFFIXES` at automation/lib/mios_var_closure.py:22).
  - *Exempted by Design Doc Marker* (automation/lib/mios_var_closure.py:135): 0 consumer-scoped files (2 files in tree).
- **Files Excluded by Extension Filter**: 1,527 files in the walk do not match `CONSUMER_GLOBS` (e.g. Markdown docs, Rust `.rs` files, C headers, JSON configurations, assets).
- **Ratios**:
  - Examined / Consumer Scope: 1,158 / 1,776 = 65.20%
  - Exempted / Consumer Scope: 618 / 1,776 = 34.80%
  - Examined / Total Repo Files: 1,158 / 3,325 = 34.83%
- **Blast Radius Estimate**:
  - If the test and emitter exemptions were removed, **44 files** would newly fail the gate.
  - Specifically, 11 emitter files (containing 58 unemitted reference occurrences) and 33 test files (containing unemitted mock references) would fail.
  - These 44 files introduce **77 new unique unemitted `MIOS_*` variable names** not emitted by the SSOT resolver and not permitted by the exact ratchet ceiling (414 names) declared in usr/share/mios/reference/var-closure-baseline.tsv:43.
  - Furthermore, across the 1,158 examined files, 414 referenced-but-unemitted names exist across 114 files, tightly locked by the bidirectional ratchet in usr/share/mios/reference/var-closure-baseline.tsv:43.

## PROPOSED FIX
No modifications are required for automation/lib/mios_var_closure.py or automation/98-drift-checks.sh. The vacuity issue described in TASKS.md:10924 was previously resolved under task task_989 (commit e0622e4bc72). Row task_944 in TASKS.md should be updated to `status: done` or recorded as superseded by task_989, accurately reflecting that the Law 9 gate operates over 1,158 consumer files and enforces a 414-item ratchet.

## FILES TO CHANGE
- TASKS.md (specifically row task_944 at TASKS.md:10924)
- .devloop/tasks.jsonl (flip task_944 status)

## NEGATIVE CONTROL
1. **Gate Failure Verification**: A planted reference `MIOS_TEST_UNEMITTED_XYZ123` placed in a consumer script causes `check_var_closure` in automation/98-drift-checks.sh:1104 to fail with exit code 1, confirmed by both manual verification and automated tests at tests/drift-gate-negatives.sh:1962.
2. **Deliverable Finding Gate**: The lane verification script `check_finding.py` validates that all path:line citations resolve. Appending an unresolvable sentinel citation to `.devloop/findings/task_944.md` fails with exit code 1 and outputs the sentinel name.

## UNVERIFIED
- We have not verified whether any of the 1,527 non-consumer-glob files (e.g. native Rust crates under `src/mios-rs/` or `tools/native/`) read `MIOS_*` environment variables via direct OS environment lookups.
- We did not audit each of the 414 baseline entries in usr/share/mios/reference/var-closure-baseline.tsv:43 to determine which should be added to SSOT emissions versus removed from consumer code.
