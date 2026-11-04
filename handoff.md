<!-- AI-hint: Operator handoff prompt for remote agents and subagents installing the playable Tamagotchi sample on real MiOS Blade environments. -->
<!-- AI-related: AGENTS.md, .agents/COORDINATION.md, tasks.jsonl, usr/share/doc/mios/tamagotchi-tui.md, task_3708 -->
# Remote MiOS Blade agents/subagents: playable Tamagotchi handoff

You are taking over **live installation and verification** of the playable
MiOS Tamagotchi sample. Use remote agents and subagents to complete concrete,
separately owned work. Proceed with the authorized user-local trial, collect
real evidence, and return a consolidated result for each actual target node.

## Objective and source

Install, debug and play `mios-tamagotchi` **live on the MiOS desktop, inside
Nested MiOS Sessions whose viewports remain visible to the operator**, on the
authorized Blade node(s). Each gameplay/debug worker owns a labelled nested
session. First use a separate user-local sample executable and a separate test
save. Image integration is a later lane.

Repository: <https://github.com/MiOS-Developer/MiOS>

Known playable sample commit:
`518d4c70e9c83a60fc2da885898ee67ca0be3985`

Before work, read:

- `AGENTS.md` and applicable local instructions.
- `.agents/COORDINATION.md` for native agent registration, delegation and leases.
- `usr/share/doc/mios/desktop-pip.md`, especially Independent desktop sessions,
  launch targets, seats, registry and device assignment.
- `usr/share/doc/mios/tamagotchi-tui.md` for the implementation and installation contract.
- `tasks.jsonl`, the canonical task list; `TASKS.md` is its generated view.

Fetch latest main without discarding local changes. Use a clean, isolated
worktree for source changes/builds. Pin the exact source used for each artifact;
start with the known sample commit or a reviewed descendant containing it.
Record the actual commit, rather than assuming whatever main contains later is
identical. Preserve other agents' branches, worktrees, changes and receipts.

Existing task ownership:

| Task | Scope |
|---|---|
| task_3707 | Playable sample delivered; read its evidence before extending it |
| task_3708 | **Your primary task:** actual per-Blade installation and play verification |
| task_3703 | Native OS-image installation and application integration |
| task_3704 | Bootstrap configurator and layered settings UI |
| task_3705 | Real terminal/font/Inputd ownership and release checks |
| task_3706 | Remaining device features beyond this playable sample |

## Delegate agents/subagents

Act as the integration orchestrator if you received the overall assignment.
Discover real authorized nodes, current participants, tools and available slots.
A session already running on a target can inventory and verify that local node.
Use existing authorized access; report unavailable targets/access precisely.

Create and run these assignments, in parallel where dependencies permit:

1. **Build subagent:** pin source, build in MiOS-DEV, run source checks, verify
   the static executable for each required architecture, and publish its source
   commit, target, checksum and artifact location through the authorized channel.
2. **Installation subagents:** one isolated lane per discovered target node.
   Each inventories its actual node/session, opens its own visible Nested MiOS
   Session, verifies artifact delivery, installs the separate user-local sample,
   and debugs/plays it live through that viewport during acceptance.
   Inventory and terminal readiness can proceed while the build is running.
3. **Verification subagent:** observe the actual live session viewports,
   independently review build/install receipts, exercise failure cases in
   disposable fixtures, and report missing visual or functional proof.

The orchestrator claims task_3708 once and creates per-node task entries after
resolving real target identities. Serialize canonical task-list updates through
one owner. Assign source files, worktrees, node install paths and save paths so
workers do not overwrite one another. Share one verified artifact per architecture
when the same source/build applies to multiple nodes.

For coordination, follow the repository's **MiOS-MCP + tmux-mcp** workflow.
Each running participant registers itself, keeps its own token private,
receives and acknowledges its own addressed relay messages, and returns a reply.
Use the native pane/workflow tools such as `mios_tmux_nested_workflow` for worker
execution. The wrapper does not register or acknowledge on the child's behalf.
Use any required coordinator lease according to the current contract.

Start visible worker heads through the native agent startup in their nested
terminal, so the combined MiOS-MCP connection binds to that caller's verified
human socket/session/pane. Unbound or HTTP clients default to private headless
servers; those do not establish a visible worker viewport. Follow the documented
binding contract and verify the actual displayed session before reporting it.

Give every subagent: its task ID, actual node or build target, exact source,
owned paths, permitted actions, positive/negative checks, and required receipt.
Report actual participant IDs and outcomes. If delegation tools are unavailable,
state that limitation and complete the useful available work without claiming
that a team ran. A queued message or started pane does not establish completion.

## Mandatory live desktop and Nested MiOS Sessions

This is a **visible live-debug/play assignment**. Run the orchestrator and
interactive workers through the live MiOS desktop. Each gameplay/debug subagent
works inside its own Nested MiOS Session, with the game terminal and relevant
debug output visible in that session's viewport. Build commands run in MiOS-DEV
and may be driven from a terminal in the visible development session.

Before starting another build or nested session, run the SSOT memory admission
check. Discover existing sessions and available devices:

```sh
mios-gen mem-admit
mios-gen nested-session --list-sessions
mios-gen nested-session --list-devices
mios-gen nested-session --help
```

Honor admission and slot limits; queue work when needed. Keep progress visible
without lowering admission thresholds or closing another participant's session.
Choose a unique sanitized seat for each worker after checking the live registry.
For one unclaimed example seat, inspect the plan before launching:

```sh
mios-gen nested-session --seat mios-tamagotchi-live --user "Tamagotchi live play/debug" --target window --no-devices --plan
mios-gen nested-session --seat mios-tamagotchi-live --user "Tamagotchi live play/debug" --target window --no-devices
```

Use a different unique seat if that label is already owned. Prefer `window`,
`side-by-side`, or a readable pinned `pip` viewport on the real desktop. Use the
MiOS Desktop Session launcher or an owned persistent native workflow: keep the
launcher/session alive throughout debugging instead of tying it to a short-lived
command timeout. Use the nested session's own terminal/selector and native pane
tools to run the worker and game **inside that session's runtime and Tmux-OS
namespace**. A parent-desktop terminal is a different execution target.

Retain the actual returned descriptor/registry entry and session identity:
seat, runtime, compositor/Wayland instance, parent window identity, output,
Tmux-OS socket and assigned input devices. Resolve them through the native tools;
do not invent socket paths, pane IDs or display environment variables. Label
viewports clearly with the owning agent, node and task so the operator can
follow concurrent sessions.

The orchestrator keeps the worker viewports visible together on the operator's
MiOS desktop. For another Blade, use the existing authorized remote-display or
nested-session mirroring route and verify the operator actually receives live
frames. The `remote` target requires a real attached, nonzero-sized served
output; a session descriptor's `serve` data describes its mirror target. Discover
and use the installed implementation, without inventing ports or remote hosts.
If only SSH/headless access is available, complete useful build/inventory work
and report **live desktop visibility blocked** until the real viewport route is
working. That node has not passed this assignment's visual acceptance.

During play and debugging:

- Keep the actual game process rendered in its live nested viewport. Show the
  terminal/debug pane alongside it as needed. Maintain readable sizing and
  responsive focus while preserving the operator's other windows.
- Use native input/pane tools against the verified worker session. Demonstrate
  an actual action and subsequent visual change, such as menu movement, feeding,
  a memory answer, growth, pause/resume or resize. Check live frame changes over
  time and record real viewport captures with their node/seat/task identities.
- Begin with isolated `--no-devices` keyboard/pointer operation. For controller
  checks, use discovered, explicitly assigned devices and existing Inputd
  ownership/release rules; preserve the parent desktop's input ownership.
- Reproduce defects in the visible game, inspect its real state/logs, make any
  needed source fix in the owned worktree, rebuild in MiOS-DEV, verify/deliver the
  updated executable, and repeat the play sequence **in the same live workflow**.
  Record the new artifact identity. Restart only the owned game when sufficient.
- Use CLI tests, PTYs, snapshots and logs as supporting evidence. Screenshots
  are receipts of the live view; they do not substitute for a viewport that is
  still updating while the worker plays and debugs.

Keep the owned Nested MiOS Sessions and their viewports open for operator review
at handoff. After exit/failure checks, relaunch the sample visibly, using the
per-node test save where appropriate; pause it if you need to protect its state.
Document who owns the continuing launcher/session and how to return to it. Ensure
that closing an ephemeral helper connection does not silently remove the live
view. Preserve operator-adopted sessions and do not close them as test cleanup.

## What already exists

The sample implements feeding, cleaning, rest, medicine, illness/death,
egg/baby/child/adult progression, a three-arrow memory game, coin rewards,
pause, confirmed reset, and versioned atomic local saves with a writer lock.

Local evidence already recorded:

- 24 Rust/CLI tests and warning-fatal Clippy pass on x86-64 musl.
- A pseudo-terminal play-through exercised care, a memory win, a thriving adult,
  pause, save/reload, competing-writer refusal, reset confirmation and restoration.
- Optimized x86-64 and ARM64 binaries passed native static-artifact validation.
- The extracted x86-64 evaluation bundle ran its standalone demo successfully.
- All 28 declared projection stages completed.

**No real Blade installation has been certified. ARM64 execution has not been
verified.** These are the remaining live results you must produce. Local tests,
a cross-build, a screenshot, or an installation command alone cannot establish
the complete per-node result.

## Build and deliver

Build inside **MiOS-DEV**, following the current native toolchain policy.
Rust 1.89 or newer is required; use the repository's pinned toolchain and lockfile.
From the source root, resolve settings for the actual architecture:

```sh
miosd native-build-settings --root . --arch x86_64 --json
```

Use `aarch64` for an ARM64 node. From `tools/native`, an x86-64 build/check is:

```sh
cargo test --locked -p mios-tamagotchi --target x86_64-unknown-linux-musl
cargo clippy --locked -p mios-tamagotchi --all-targets --target x86_64-unknown-linux-musl -- -D warnings
cargo build --locked -p mios-tamagotchi --release --target x86_64-unknown-linux-musl
miosd native-artifact-check target/x86_64-unknown-linux-musl/release/mios-tamagotchi --root ../.. --arch x86_64
sha256sum target/x86_64-unknown-linux-musl/release/mios-tamagotchi
```

For ARM64, build and validate `aarch64-unknown-linux-musl` with `--arch aarch64`.
Run executable tests on a compatible runtime and label cross-build-only results.
Transfer using the node's authorized channel, then compare the received binary's
checksum with the build receipt before running it.

If the operator supplies the cloud-built evaluation archives, these are their
archive checksums:

| Archive | SHA-256 |
|---|---|
| `mios-tamagotchi-sample-x86-64.tar.gz` | `4c2d86b82481231bb27c347d98c02e9d458464f2a75b5a7384ae7591537266b8` |
| `mios-tamagotchi-sample-arm64.tar.gz` | `5444b34798aebd2d3bc5e0bd659381eb36ca7939980eb5b59eb10d99d8a9b175` |

They contain the executable, README, SOURCE provenance and payload SHA256SUMS.
They are session-provided evaluation files, not GitHub release assets. Do not
assume another session's `/workspace/shared` exists on your node. Rebuild in
MiOS-DEV for the live integration receipt; evaluation archives are not certified
OS-image builds.

## User-local installation and first run

On each authorized target, record node/guest identity, architecture and live
MiOS version. Work in the selected user-facing MiOS session; hardware/boot/network
roles are outside this application trial.

Use the verified artifact's actual location when installing. Preserve an existing
sample executable before replacing it. Install as the normal user in
`$HOME/.local/bin/mios-tamagotchi-sample`; keep the normal OS executable separate.
No sudo, bootc switch, new service, or live TOML replacement is needed.

Run the installed sample executable:

```sh
"$HOME/.local/bin/mios-tamagotchi-sample" --demo
"$HOME/.local/bin/mios-tamagotchi-sample" --sample
```

`--demo` is read-only and should finish with a memory win. `--sample` uses
embedded vendor settings, so older live MiOS settings need no update. It is
save-free unless you explicitly supply `--save PATH`.

For a separate local test pet, use the same command to start and resume it:

```sh
"$HOME/.local/bin/mios-tamagotchi-sample" --sample --save "$HOME/.local/state/mios/tamagotchi-blade-sample/pet.json"
```

Select a distinct save per node/user or concurrent instance. The lock coordinates
local processes, not distributed nodes; do not share the pet over a network
filesystem or remove a lock file while its writer is running.

## Live acceptance sequence

First verify that the correct live nested viewport is visible on the operator
desktop and that an action produces a real frame/state change. Keep it visible
throughout the following checks. Record pass/fail/unverified separately per node:

1. **Real play:** hatch the egg; feed, clean, rest, treat illness when needed,
   and win a memory round through the UI. Reach a thriving adult. Default growth
   is about six seconds to hatch and two minutes to adult; report any different
   settings. Never edit age/needs/coins to manufacture a successful play result.
2. **Controls:** arrows/Tab select actions; Enter/Space activates. Lowercase
   `f/g/c/r/m/p/s/n/q` provide care, pause, save, reset and quit shortcuts.
   Memory guesses use the four arrows after the displayed pattern disappears.
   Test Inputd capture/release through the actual owned terminal/tmux pane.
3. **Rendering:** verify ASCII on the available VT/console, a suitable Unicode
   renderer on a supported terminal, resizing and a tiny pane. Quit must still
   work when the sprite cannot fit. Leave unavailable fonts/terminals unverified.
4. **Persistence:** save and quit, reopen the same explicit path, and compare
   needs/age/coins/pause. Verify offline time stays frozen and pause stops progress.
   Cancel a new-pet prompt and confirm the current pet is retained; confirm reset
   only in the disposable test pet.
5. **Writer exclusion:** while one interactive session owns the save, start a
   second **interactive** session with that same path. It must refuse before raw
   mode. A piped/headless invocation is read-only and does not test writer locking.
6. **Failure paths:** use separate disposable fixtures for malformed/oversized
   saves and unsupported schema. Verify refusal, unchanged fixture bytes and a
   usable terminal. Do not corrupt the active test pet or operator data.
7. **Read-only output:** test `--sample --snapshot` and `--sample --save PATH
   --snapshot` with redirected output. Verify plain ASCII, unchanged save bytes
   and no clock advancement. Verify normal/error exit restores the terminal.
8. **Held keys:** releases never act and explicit repeat events are filtered.
   Legacy terminals may report held keys as fresh presses; record the actual
   controller/terminal behavior instead of assuming universal repeat detection.

Automate through the native terminal tools where useful, and retain real captures
and command results. Label automated play as automated. Keep failures and blocked
cases visible while continuing independent work on other available nodes.

## Finish and return evidence

The orchestrator reconciles subagent results, verifies each receipt, and records
canonical task evidence. Complete task_3708 only for the scope actually verified;
leave missing nodes/cases explicit or in separately owned follow-up tasks.

For necessary source fixes, use isolated, function-named `mios-` worktrees and
regression controls. Follow the repository's approved publication workflow and
existing operator authorization. Keep image delivery, bootstrap configuration,
terminal integration and new gameplay features in their respective task lanes.

Known broader baseline findings were unchanged at handoff: rust-categories 35,
module-test-coverage 1, no-inert-ssot-tables 1 (dashboard), and template conformance
6. Recheck against your source baseline, fix introduced regressions and report
pre-existing failures without weakening gates or claiming global CI is clean.

Return a concise summary plus a receipt table containing:

- Node/guest identity, MiOS version, architecture and owning agent/subagent.
- Task ID, exact source commit, MiOS-DEV build context and artifact SHA-256.
- Actual installed executable path, configuration mode and per-node save path.
- Nested seat/descriptor, parent window/output, Tmux-OS target, live viewport
  viewing route, and evidence that it remained visible and updating.
- Terminal/controller tested and each acceptance check's outcome.
- Current viewport/session owner and how the operator can resume the still-open game.
- Evidence/log/capture locations, defects, blockers and unexecuted cases.
- Any fix commits and remaining integration tasks.

Do not include credentials or relay tokens. End with what is running successfully
on each real node, where its **still-live nested viewport** is visible, and what
still needs work. Proceed with desktop/session discovery and concrete subagent
assignments now.
