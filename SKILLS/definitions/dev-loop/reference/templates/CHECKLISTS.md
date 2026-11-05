# Quality & Gate Verification Checklists

## A. Pre-Flight Checklist (Run Before Any Modification)
- [ ] Upstream documentation consulted; no deprecated APIs used.
- [ ] Base branch is clean (`git status --porcelain` is empty).
- [ ] Git worktrees configured under `.worktrees/` and present in `.gitignore`.

## B. Iterative Implementation Checklist (After Every Edit)
- [ ] File is non-empty (`wc -c > 0`).
- [ ] Syntax compiles cleanly without warnings.
- [ ] Edit written atomically via `.tmp` staging.

## C. Merge Gate Checklist (Before Reconciling Worktree)
- [ ] Worktree has zero uncommitted changes.
- [ ] Pre-merge test gate passes inside the worktree.
- [ ] Merged using `git merge --no-ff`.
- [ ] On conflict: executed `git merge --abort` immediately; preserved branch and worktree.
