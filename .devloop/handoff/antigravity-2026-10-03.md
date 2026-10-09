<!-- AI-hint: Work brief for the local Antigravity agent: five MiOS tasks that collide with no running cloud lane, branch-per-task, verified and merged to main by the cloud session. -->
# Antigravity work brief (2026-10-03)

From the cloud dev-loop session. These five tasks are claimed for you
(`owner = antigravity` in `tasks.jsonl`); nothing else running touches their
files. Each task row in `tasks.jsonl` holds the acceptance criteria and the
positive + negative controls -- that row is the definition of done.

| Task | What | Why you |
|---|---|---|
| task_3425 | Hermetic Windows wallpaper cross-build inside MiOS-DEV | needs a real Windows/WSL host |
| task_3481 | Remove audited dead PowerShell in BOTH repos (mios.git + mios-bootstrap.git, Law 15) and settle the mios-node Rust parity stubs | real pwsh + Windows to run the Pester suites |
| task_3483 | agent-pipe: one lock order for the priority gate and endpoint semaphores | isolated to agent-pipe |
| task_3484 | agent-pipe: inject live callables into vram_scheduler.configure | isolated; you can exercise a live lane locally |
| task_3482 | Drop stale AI-functions lines from the harvested manual | small, isolated |

## Do not touch (owned by running cloud lanes)

`Containerfile`, `.devcontainer/**`, `automation/` phase scripts,
`[profiles]`/`[hardware_classes]`/`[image]`/`[platforms.*]`/`[rust.categories]`
in `mios.toml`, `tools/native/mios-resolver`, `src/mios-rs/mios-gate`,
`tasks.jsonl` (report evidence in your branch's commit message instead).

## How to deliver

1. One branch per task off latest `origin/main`: `agy/T-NNNN`.
2. Test first: show the negative control fails on the unfixed tree, then fix,
   then show it passes and that the planted violation is named.
3. Regenerate with `bash tools/sync-generated.sh` and
   `python3 tools/roadmap-index.py`; run `bash tests/run-suites.sh lint`,
   `python3 tools/ci-suites.py --check`,
   `python3 tools/sync-bootstrap.py --check` and the drift checks the row names.
4. Explicit-path staging, no secrets, no ceiling raised, no test skipped.
5. Push the branch (`git push -u origin agy/T-NNNN`); do not push to main.
   Put the before/after control output in the last commit message.

The cloud session re-verifies each branch, merges it to main once the
running lanes have landed, and flips the task to done with your evidence.
