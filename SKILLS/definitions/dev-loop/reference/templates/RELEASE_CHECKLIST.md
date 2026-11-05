# Trunk-Based Release & Worktree Shipping Checklist

**Release Target:** `[e.g., main or trunk]`  
**Feature / Lane Branch:** `[e.g., lane-backend or feat/auth-v2]`  
**Proposed Release Tag:** `[e.g., v2.1.0]`  
**Shipping Driver:** `reference/ship.py` (`/ship`, `/sh`)  
**Date:** [YYYY-MM-DD]  

---

## 1. Pre-Flight Verification Gate
All pre-flight checks must pass before triggering branch merge:

- [ ] **Zero Uncommitted Changes:** `git status --porcelain` is completely empty.
- [ ] **Two-Sided Verification Passed:** Full test suite executed with exit code 0.
- [ ] **SCOPE Review Passed:** `REVIEW.md` generated with zero blockers.
- [ ] **Upstream Invariants Verified:** No breaking changes unaccounted for.
- [ ] **Stopping Condition Met:** Goal engine confirms stopping conditions satisfied (`GOALS.md`).

---

## 2. Atomic Merge & Reconciliation
- [ ] **Target Checkout:** Switched to target trunk branch cleanly.
- [ ] **Merge Execution:** Merged with `--no-ff` preserving lane history and audit trail.
- [ ] **Zero Merge Conflicts:** Merge completed without conflict or manual resolution aborts.
- [ ] **Merge Commit Recorded:** SHA recorded in `.devloop_artifacts/ship_[timestamp].json`.

---

## 3. Worktree & Environment Hygiene
- [ ] **Worktree Pruning:** `git worktree prune` executed; directory cleaned up.
- [ ] **Ephemeral Branch Removed:** Feature branch deleted locally and remotely (if tracked).
- [ ] **Lockfile Cleanliness:** Zero stale `.git/index.lock` or temporary PID files remaining.

---

## 4. Release Documentation & Versioning
- [ ] **CHANGELOG.md Updated:** New section added under release version following Keep-a-Changelog.
- [ ] **Version Bump:** `package.json`, `pyproject.toml`, or `Cargo.toml` bumped to new semver.
- [ ] **Git Tag Created:** Signed git tag created (e.g., `git tag -a v2.1.0 -m "Release v2.1.0"`).
- [ ] **Release Brief Exported:** `SHIP_REPORT.md` written to repository root.
