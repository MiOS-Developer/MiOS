<!-- AI-hint: Current remote recovery entrypoint and milestone publication status. -->
# Cloud recovery and startup repair

After PR #65 is merged, resume this work from `origin/main`. Before the merge,
use its head branch, `claude/code-session-lryqdr`, for review. This recovery
started from main `4bf38f52b7d5103c5b6ba6fada6a440e55475062` and
also preserves the later main handoff commit
`c958dd4a07dbb56208b769d6f9e831017faf11fd`.
The sections below this entry retain the historical source handoff verbatim.
The voxel R3 and SearXNG D lanes are already merged in the PR at
`403114b99740c1701b3cb09a88497d3cf3b8aed5`; do not merge them again based on
an older handoff's pending status. HUD, camera, gesture and R4/menu/root WIP
remain preserved, with review and integration pending.

The current assignment covers both portable source recovery and the reported
Codespace failure to load MiOS. The repository is available; direct inspection
of the selected Codespace is blocked before authentication: this cloud
environment's enforced proxy rejects CONNECT to `api.github.com` with HTTP 403.
The user reports being authenticated; GitHub repository pushes work. This
session exposes policy inspection but no policy-edit or Codespaces tool.
No unpublished Codespace files or live runtime have been recovered or certified.
Native MiOS relay/tmux and a MiOS-DEV builder are unavailable in this cloud
executor; platform subagents are coordinating
isolated source lanes. No live session transfer is claimed.

## Resume without discarding work

In an existing clone, first preserve local modifications and use a fresh,
collision-checked worktree. After GitHub reports PR #65 merged, use the main
route below. The following names must not already be in use:

```sh
git fetch origin refs/heads/main:refs/remotes/origin/main
git worktree add -b mios-recovery-resume ../mios-recovery-resume origin/main
cd ../mios-recovery-resume
sha256sum -c .devloop/handoff/mios-source-checkpoints.bundle.sha256
git bundle verify .devloop/handoff/mios-source-checkpoints.bundle
git bundle list-heads .devloop/handoff/mios-source-checkpoints.bundle
```

The bundle has three prerequisites, all available from the full main history.
For a shallow clone, fetch the missing history before importing. Inspect any
existing `refs/remotes/mios-handoff/*` first; do not force-update a divergent ref:

```sh
git fetch .devloop/handoff/mios-source-checkpoints.bundle 'refs/heads/*:refs/remotes/mios-handoff/*'
```

## Milestone publication

The orchestrator serializes integration and remote publication. Each subagent
works in its own function-named worktree, commits only its assigned files and
writes a tracked receipt under `.devloop/handoff/mios-<purpose>.md`. Each receipt
records source, branch, changes, actual positive/negative checks, blockers and
next actions. Milestone commits are pushed to the PR branch as they become
reviewable; fetch that branch to recover the latest committed state. Work not
in a published commit remains local, and a queued message is not a push receipt.

Current lanes:

| Lane | Worktree branch | Receipt |
| --- | --- | --- |
| Recovery audit | mios-cloud-recovery | `.devloop/handoff/mios-recovery-audit.md` |
| Recovery inventory | mios-checkpoint-manifest | `.devloop/handoff/mios-checkpoint-manifest.md` |
| Codespace startup | mios-codespace-startup | `.devloop/handoff/mios-codespace-startup.md` |
| CI registration | mios-recovery-ci | `.devloop/handoff/mios-recovery-ci.md` |
| Integration and publication | mios-cloud-recovery | `.devloop/handoff/mios-cloud-recovery.md` |

The operator explicitly requested finishing PR #65 and merging its source and
handoffs to main. The integration owner publishes the reviewed PR using an
expected head SHA; remote successors recover its committed state from main.
The existing live-installation requirement in `.agents/COORDINATION.md` remains:
record the actual target and artifact receipt after publication, or record the
concrete access blocker when installation cannot run. This executor cannot
reach the selected live Codespace. Source publication does not certify a live
rebuild or deployed MiOS.

The source repair replaces the absent post-start gate, installs actual
Cargo-reported executables using locked workspaces, and selects the fresh
catalog for both lifecycle and editor processes. After updating an existing
Codespace's clean checkout to the merged main, perform a full container rebuild;
a reconnect alone retains the old container. See `.devcontainer/README.md`.

---

## Latest verified status

The post-publication receipt is in `.devloop/handoff/mios-completion-status.json`.
It records the exact 4bf38f52 build, user-local installation, live pinned PiP,
localhost route and the recovered **CONFIRMED D4** verdict (69 SearXNG + 7 nftables
checks, all 84 planted failures caught). The bootstrap mirror patch is preserved
alongside it for review; it has not been applied to a separate repository.

This follow-up changes handoff documentation and artifacts only. Application
source and build inputs remain those verified at 4bf38f52; the running binary's
actual build provenance stays at that commit. R4 visuals, performance, merge
conflicts and full hardware acceptance are still open. A cloud reply was
forwarded one-way to a session named Code session, without a read receipt or
confirmation that its identity matches the intended importing session.

---

<!-- AI-hint: API-published source snapshot and exact Git-history bundle for remote continuation. -->
# Recover this handoff after Git transport failures

Authenticated Git push failed with HTTP408 and moved no refs. This checkpoint
was published through the repository Git objects API instead. The source
snapshot is the prepared local checkpoint
fc89aae34d836f4902c6ccd40341fa0c3db87e89. The API commit has a different SHA;
use the current origin/main SHA as the publication receipt.

The bundle contains the EXACT original commits and seven lane histories.
It also contains the local checkpoint's WIP patches, design context and
root-change patches. It is an incremental bundle against the previous
origin/main history at c1e55b7582e7335b5f504b393416573b2e34d5d6.

In a clone containing that history, restore the source objects:

    git fetch origin main
    sha256sum -c .devloop/handoff/mios-source-checkpoints.bundle.sha256
    git bundle verify .devloop/handoff/mios-source-checkpoints.bundle
    git fetch .devloop/handoff/mios-source-checkpoints.bundle 'refs/heads/*:refs/remotes/mios-handoff/*'
    git show mios-handoff/mios-cloud-handoff:handoff.md

If bundle verification reports missing prerequisites in a shallow clone,
fetch the full main history first. Do not reset the current branch or overwrite
uncommitted work. Create collision-checked worktrees from the recovered refs.

The original source refs are recovered under refs/remotes/mios-handoff/.
They are not claimed as successfully pushed origin branches. Main contains
the Root integration snapshot; renderer/ports conflict resolution and camera/
HUD review remain pending. The original local f576f22 and all source lane
commits become locally inspectable after the bundle fetch.

This is an engineering source handoff, not a certified release. Do not claim
combined CI, visual acceptance, or live deployment merely from this publication.

---

<!-- AI-hint: Portable engineering handoff for repository-based continuation; current checkpoint is not a certified release. -->
<!-- AI-related: AGENTS.md, .agents/COORDINATION.md, tasks.jsonl, .devloop/handoff/mios-wip-manifest.json -->
# Current portable engineering handoff

This checkpoint answers the operator's request to make all current source and
unfinished work recoverable by a remote engineering session. Read this section
before the historical Blade installation prompt below. The publication is a
source handoff; combined CI, visual acceptance and live rollout are unfinished.

## Recover the exact source

Main contains the source snapshot of Root's seven local main commits through
f576f22f1b91c9cffa1070dd55b6eb5c296b6a01 and integration
fb49657e85fbbe13d8620b6c8e674223e25a558e. Original commit ancestry and all
seven source refs are preserved in the Git bundle described above. Branch
names in this table identify bundled refs; remote branch creation did not
succeed through the timed-out Git transport.

| Branch | Exact source | Disposition |
| --- | --- | --- |
| mios-mons-input-completion | fb49657e85fbbe13d8620b6c8e674223e25a558e | Included in main; application/Inputd/Portal/CI integration |
| mios-mons-voxel-overlay | 6b20513e1f657f5159c02384552d7b2294f585bc | Preserved branch; main merge conflicted in render.rs, sprite.rs, ui.rs and game documentation and was aborted |
| mios-ci3756-ports | 58b3752d415806f7cd9d43a6d04090757434eb3e | Preserved branch; main merge conflicted in roadmap, GUI test, manifest, blade.sh, tool registry, SSOT and corpus projection and was aborted |
| mios-mons-menu-geometry | 26eb01d492382da7a59d7b17cf5e24af6be55f27 | HUD geometry/painter implementation; integration and independent review pending |
| mios-mons-camera-follow | 0e7c693b8242d0342049cfd90b659ab2da1301a3 | Camera settings/follower implementation; integration and independent review pending |
| mios-inputd-focus-gesture | 3f3dac3beab2f2a6bb7efe009753c2a23daa49ee | Gesture implementation; configurator integration and final verification pending |

Use new isolated worktrees and merge/reconcile those branches. Do not assume
that a branch's implementation report certifies the combined tree. Preserve
the additional assertions in the integration branch when reconciling the game.

## Preserved unfinished work

Files in .devloop/handoff are tracked, portable inputs. Patches are losslessly
compressed to preserve unified-diff context bytes without whitespace damage:

- mios-render-r4.patch.gz: the actual uncommitted render.rs performance work,
  +1080/-89, based on renderer 6b20513e. Only render_cost checks were reported;
  full verification is pending and the 1.15x target remains unmet.
- mios-menu-navigation.patch.gz: ui.rs/input.rs cardinal menu navigation and
  actual-binding hints based on fb49657e. This is WIP, not a complete HUD.
  Its MiOS-DEV formatting job was queued by memory admission; no pass claimed.
- mios-root-tracked.patch.gz: the actual local .gitignore, chrony.conf and
  hyprland.lua changes against f576f22. Review ownership and correctness before
  applying; the deployed root was not reset, rebased or overwritten.
- mios-root-untracked-code.patch.gz: three dispatcher symlinks and the container
  engine CDI unit drop-in from the deployed root, preserved for review.
- mios-wip-manifest.json: exact bases, file lists and payload SHA-256 hashes.
- mios-multiplayer-design.md, mios-multiplayer-task-proposal.md and
  mios-mon-world-research.md: the operator's design/research context copied from
  local state. Proposals are not canonical completed tasks or implemented code.

Apply a patch only to its recorded base after checking it:

    gzip -dc /path/to/mios-render-r4.patch.gz | git apply --check
    gzip -dc /path/to/mios-render-r4.patch.gz | git apply

Do not apply all patches blindly to main. Untracked runtime models, agent
datastores, first-boot progress/lock files, credentials and relay leases were
excluded. Live sockets, pane IDs and runtime paths from older receipts are
historical observations, not valid remote targets.

## Verification state and remaining work

Renderer R3 was reported build-green in MiOS-DEV: 409 unit checks (2 ignored)
and 13 CLI checks, fmt, fatal Clippy and static artifact validation. Its binary
SHA-256 is f8656074db8ea767112c35cd475448210bff70370d342ae550db7393e0bb59f0.
Visual acceptance remains PARTIAL/FAIL: smallest ASCII/8-bit and monochrome
legibility, face quiet zones, braille ring, fox brows, oblique cat eyes and
sleeping-face strokes need work. Room cost was 0.88..1.08x baseline; Pet/Face
1.25..1.94x misses the required <=1.15x bound. Preserve the good half-block and
quadrant cases while fixing those failures. No successful R4 final build or
visual cutoff is claimed.

D4's cached implementation and entry-point fixes are at 58b3752d4. Earlier
reports caught 77 planted failures and fixed Setup's param-block ordering.
The source participant subsequently recovered the final adversarial verifier's
CONFIRMED verdict: 69/69 SearXNG and 7/7 nftables checks, all 84 planted failures
caught, no skips and an empty FOLLOW_UP register. See the latest status receipt
for the verifier details and nonblocking near-misses. Retain shipped-layout
SearXNG value-or-skip resolution, loopback-only reachability, skipped direct
LAN forwarding and sandboxed firewall exit-78 propagation.

HUD lane reports 384 passing checks, fmt/clippy/static build, artifact
e75a5cdda418c436060ba28b3a6bf205deb56c9d8b4e5c990a9ec94b3a128f6e.
The pure geometry/painter API still needs Root's menu consumer. Inspect actual
ratio types, complete off-page navigation, selected-item reveal, bounded
portrait/tiny layouts and disabled/focus rendering before merging.

Camera lane reports 383 unit plus 13 CLI checks, fmt/clippy/static build,
artifact 3a76fb8665b2b4a8ab241ce495b7846b63c3c28dc0ca9d61ec41cfe74af4a0c5.
Review the first-order follower, shortest yaw, frame-time clamp, bounds,
reset behavior, idle cadence, paused/sleep invariance and output identity.
This is not proof of proportional analog control or physical acceptance.

The gesture review found missing configurator fields. Inspect the actual
focus_gesture parser/table: reports disagree about the number/names of its
parameters. Do not copy an invented six-field UI. Complete native
capture/release and parent-session return, actual controller prompts,
keyboard/mouse/touch navigation and focused/headless behavior.

Inputd common app-action isolation at26641b342 was verified with232 native
checks and compiled negative controls and installed user-locally as artifact
ebc8f7b9aae632e9dbcac863bb5a1a1b8bd7de43de2d0bdac0d330a5e0409793.
The public absolute Inputd entrypoints were still older at the last receipt.
Before applying new G2 vendor policy to live /, upgrade and verify every
absolute entrypoint, then deploy policy in the documented order. Temporary
right-stick focus suppression stays until the deliberate-flick path passes.

Physical menu Down/Cross/Circle passed earlier. Capture release transitioned,
but full nested controller return failed; an operational parent handoff was
performed separately. Physical cursor retest was unavailable. Phone, ARM,
VT/watch and multiplayer acceptance are not certified. Most recently the
native nested-session registry was empty: earlier retained PiP receipts do
not establish a viewport still running now. Reopen an admitted, labelled PiP
and verify live frames before claiming live acceptance.

## Continue and publish

1. Read AGENTS.md, .agents/COORDINATION.md, tasks.jsonl and the application docs.
   Use native per-participant registration and addressed receive/ACK. Root is
   the sole canonical task-list/integration publisher; obtain current ownership
   before writing shared source.
2. Reconcile finite renderer/ports cuts, HUD/camera and menu WIP in disjoint
   worktrees; keep failures and unfinished work explicit.
3. Build only in MiOS-DEV using the pinned toolchain/lockfile and actual source.
   Run SSOT memory admission and session/device inventory before builds/sessions.
   Honor the heavy-build lock and resource limits. Never lower admission gates.
4. From tools/native, run locked musl tests, fatal Clippy, release build and
   native-artifact-check, plus meaningful compiled negative controls. Use the
   root-level PowerShell mount and writable HOME for relevant fixtures.
5. Run projections and required combined gates. Baseline global findings
   previously included rust-categories35, module coverage1, inert SSOT1 and
   template6; report actual current results without increasing ratchets.
6. The operator authorizes main publication and requires immediate live
   installation after each MiOS main push. Preserve user changes and adopted
   pets, verify checksums, open actual visible PiPs and record each real node's
   outcome. A repository push is not live installation.

The build filesystem was98% full. Selective cleanup recovered11.7GiB from
inactive Cargo intermediates older than14days while retaining executable and
release outputs. Its last observed free space was about22GiB; the main root
had about123GiB. Recheck rather than assuming these resource observations persist.

Local model/API connectivity failures and queued memory admission interrupted
the local workflow. The remote importing session can continue source work
from these Git objects without access to that machine. No tool here certifies
that a message through MiOS relay reaches a different remote-session transport.

---

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
