# Definition of Done (DOD)

An iteration or task lane is considered **DONE** when and only when all following criteria are satisfied:

## 1. Code Integrity & Hygiene
- [ ] Edits address root cause directly; no symptom-level workarounds or fresh suppressions.
- [ ] All touched files verified non-empty (`wc -c > 0`) and syntactically clean (`py_compile`, `bash -n`, `cargo check`).
- [ ] Atomic file write pattern followed (`.tmp` -> `mv`).

## 2. Verification Discipline
- [ ] Positive control passes against real inputs.
- [ ] Negative control deliberately fails when a defect is introduced, confirming test gate potency.
- [ ] Test harness verified to have executed non-zero tests (no empty-set passes).
- [ ] No skip-as-pass: Missing SSOT or test fixtures fail with non-zero exit code.

## 3. Git & Worktree Hygiene
- [ ] Changes executed in dedicated worktree (`.worktrees/<worker_id>`).
- [ ] Zero blanket staging (`git add -A` prohibited); only explicit file paths staged.
- [ ] Pre-merge test gate passes within the worktree.
- [ ] Merged atomically via `--no-ff` with clean conflict abort rollback.
