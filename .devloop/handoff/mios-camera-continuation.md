<!-- AI-hint: Recoverable camera-continuation milestone with exact source ancestry, known defects and unrun native verification. -->
<!-- AI-related: handoff.md, tools/native/mios-tamagotchi/src/ui.rs, tools/native/mios-tamagotchi/src/config.rs, task_3774 -->
# Camera continuation

Base: `69fa06efe04f6422bbaec8b4ea0b953c24df2dd5`.
Branch: `mios-camera-continuation`. Owner: camera continuation lane.

## Milestone 1: recover the finite camera delta

Only the three camera commits after `fb49657e` were transplanted. The full
historical lane was not merged; its original ref remains preserved.

| Original source | Recovered commit |
| --- | --- |
| 19b53c6c2 | ff591ce5f |
| 7d43a2ffe | 50eb020ce |
| 0e7c693b8 | 11d620656 |

These commits merge without conflicts but remain unverified source. Independent
review found realtime camera output overwritten by `animate`, nonterminating
default reset, unscaled stop thresholds, and stale follower pan on subject
changes. This milestone preserves the input before repairing those defects.

Owned files are camera regions of `ui.rs`, `config.rs`, the configurator and
its existing security/form tests, vendor `mios.toml`, and this receipt. HUD
implementation, canonical task registry, root handoff and whitelist updates
remain owned by the integration coordinator.

No native compilation was attempted outside MiOS-DEV. Native checks, compiled
negative controls, static artifact validation and live acceptance remain
pending. A source push does not certify any of those results.

## Milestone 2: repair the reviewed camera defects

The realtime follower now owns displayed camera axes after the interactive loop
starts. Frame-only snapshots retain their deterministic frame interpolation and
keep the follower state synchronized. Captured view-blend yaw/pitch endpoints
also follow realtime orbit/tilt increments, so a zoom or view blend cannot pin
those axes between pet animation frames.

Axis settlement is independent of reset bookkeeping. Nonzero reset finishes
when its axes settle or its maximum accumulated camera transition time expires.
At that deadline it cuts to the reset target even if configured speed limits
are slower; zero cuts immediately. New camera input cancels that reset deadline.
This duration uses the same clamped elapsed time as camera motion, so suspension
does not create an unbounded step. The configurator, vendor settings and native
field comments describe that same policy.

The stop threshold scales each axis's own input step by the configured
per-mille. Ordinary settlement respects that frame's rate budget. Zero,
negative or NaN elapsed time does not move axes. Scene-subject changes clear
both displayed and floating follower pan, preserving the existing subject-cut
contract.

Six native behavior tests were added to the recovered tests. They exercise the
actual realtime-step / pet-animate / draw sequence and drawn yaw/pitch; extra
camera redraws with active, paused and sleeping pets; the default 350ms reset
deadline and return to idle cadence; stop-threshold dimensions and rate budget;
subject-pan clearing; and interrupted reset. The recovered spike-time test now
compares a 1000ms input to the actual 250ms clamp, and instant reset is checked
through the next realtime/animate sequence.

Executed locally, without native compilation:

- `python3 tools/test_configurator_security.py`: 11 tests passed.
- Official stable `rustfmt` 1.10.0 (Rust 1.99.1), edition 2021: both changed
  native files parse and format successfully; `--check` passed.
- `git diff --check`: passed.

Native tests were added but have NOT been compiled or run. The runtime lacks a
MiOS-DEV builder. Formatter runtime libraries were obtained only to format the
source; no compiler or native build was run. Before integration, run in the
authorized native build environment from `tools/native`:

```sh
cargo test --locked -p mios-tamagotchi --target x86_64-unknown-linux-musl camera_plain_fallthrough_tests
cargo test --locked -p mios-tamagotchi --target x86_64-unknown-linux-musl
cargo clippy --locked -p mios-tamagotchi --all-targets --target x86_64-unknown-linux-musl -- -D warnings
cargo fmt --all -- --check
```

Run compiled negative controls in disposable copies, one defect at a time:

| Plant in actual native implementation | Required assertion failure |
| --- | --- |
| Always run the legacy axis block in `animate` | realtime pose survives animate/draw |
| Gate reset completion on `!camera_unsettled()` again | default reset deadline / idle cadence |
| Return raw per-mille as the axis epsilon | stop threshold dimensions |
| Remove floating pan clear from `set_scene_for_stage` | subject-change pan clear |
| Stop updating captured view-blend yaw/pitch endpoints | drawn yaw/pitch at unchanged pet frame |
| Restore an unbounded camera elapsed step | spike-time equivalence to the 250ms bound |

Each plant must compile and fail a behavior assertion with nonzero tests; a
compile failure is not a successful negative control. These plants have NOT
been executed here. Proportional analog control, full combined CI, static release
artifacts, real controller/desktop interaction and installation remain pending.

## Resume

```sh
git fetch origin refs/heads/mios-camera-continuation:refs/remotes/origin/mios-camera-continuation
git worktree add --detach ../mios-camera-review refs/remotes/origin/mios-camera-continuation
git -C ../mios-camera-review log -5 --oneline
```

Use a collision-free directory and retain the exact fetched SHA. The
coordinator will prepare a draft continuation PR for remote compilation before
main integration. No live deployment is claimed.
