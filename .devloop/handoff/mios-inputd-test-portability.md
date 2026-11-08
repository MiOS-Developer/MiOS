<!-- AI-hint: Current global pickup is main; historical stage receipts follow. -->
## Current main pickup

Resume from main and `mios-continuation-milestones.json`. Combined native results
include442 game unit and13 CLI passes (3 ignored),248 controller passes, fatal
Clippy and format. The keybinding test suite passes31 tests. All29 projection
stages completed twice without second-run changes.

All248 controller tests pass, including the real private-socket worker barrier and cancellation fixtures. Production process detection is unchanged.

Exact source/provenance and raw records are in `mios-native-verification.tar.gz`.
Live Codespace, physical input, full image and R4 acceptance remain open.

---

<!-- AI-hint: Historical source-stage receipt; current summary above supersedes earlier unrun or branch-pickup notes. -->

<!-- AI-hint: Native input dispatcher test portability and exact worker-inspection barrier receipt. -->
# Inputd session-worker test portability

Source base: published MiOS main `2c1e0942bcd9c6538bc61b70268742003b82f57a`.
Local lane: `mios-inputd-test-portability`. Root serializes integration and
publishes only main; this lane does not push a worker branch.

The existing revocation integration test timed out at its initial inspection
barrier, before worker cancellation or fresh-worker delivery. The ephemeral
MiOS-DEV executor exposes `/proc/<pid>/status` and `/proc/<pid>/cmdline`, but
omits `/proc/<pid>/task/<tid>/children`. A direct diagnostic spawned a child
sleep process and confirmed that child's `/proc` entry exists while the parent
thread's `children` file returns ENOENT. The previous helper converted that
missing file to an empty child list and therefore could never see inspection.
The unchanged runtime timed out X and expired Y while this test barrier waited.
Two isolated executions of the existing compiled test reproduced the failure.

A separate race existed in the same helper: it accepted any `display-message`
for the private fixture socket. Live process inspection observed two direct
children: the app verifier querying `#{pane_pid}` and the dispatch worker
querying `#{pid}|#{window_id}|#{pane_id}|#{session_name}`. The former could
satisfy the old barrier without proving the action worker was inspecting.

Only the test fixture changes. It scans numeric `/proc` entries, verifies the
exact parent PID, and requires the private socket, exact `display-message`
argument and exact worker identity format. A new regression accepts the worker
query and rejects the verifier query, wrong/missing parent, wrong socket and a
near-matching command. Production dispatch, cancellation, queue expiry, the
three-second fixture timeout and the 500ms cancellation assertion are unchanged.

Verified inside the owned ephemeral MiOS-DEV builder, serialized through
`/workspace/mios-native-builder-state/mios-heavy.lock`:

```sh
cargo test --locked --target x86_64-unknown-linux-musl -p mios-inputd --lib runtime::tests::session_worker_integration -- --test-threads=1
```

Result: 9 passed, 0 failed, 0 ignored, 239 filtered out. This includes actual
revocation, stale-input rejection and fresh-worker delivery with their original
assertions. Compilation completed successfully. The run used this worktree as
its source and the shared builder target/cache. The log is an executor-local
verification file, not a committed runtime artifact:
`/tmp/mios-inputd-test-portability-native.log`.

After that run, official rustfmt output normalized only the new helper (line
wrapping and a single-expression closure). No unrelated runtime formatting was
imported. `git diff --check` passes.

Next: root integrates this local commit, whitelists this receipt, then runs the
full native inputd suite on the combined source and publishes its main checkpoint.
This receipt establishes the scoped integration result only; it does not claim
full combined CI, a release image, remote Codespace recovery or live devices.
