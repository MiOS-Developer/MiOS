<!-- AI-hint: Playable local virtual-pet sample, safe save contract and per-Blade installation handoff. -->
<!-- AI-related: tools/native/mios-tamagotchi, usr/share/mios/mios.toml [tamagotchi], task_3707, task_3708 -->
# Playable MiOS Tamagotchi sample

The native `mios-tamagotchi` sample now supports feeding, cleaning, rest,
medicine, illness, death, accelerated growth, a three-arrow memory game,
coin rewards, pause, confirmed pet reset, and versioned local saves.
The goal is a healthy, happy adult. Social play, breeding, shops, multiple
mini-games and full device emulation remain future work under task_3706.

## Try it without changing a live MiOS installation

```sh
mios-tamagotchi --sample
mios-tamagotchi --sample --renderer ascii
mios-tamagotchi --sample --renderer half-block --color indexed
mios-tamagotchi --demo
```

`--sample` uses settings embedded from the build's vendor SSOT, so an older
MiOS node does not need new live TOML fields. By default this mode creates no
save directory and changes no live configuration. To explicitly opt into a
separate sample save and resume it on the next run:

```sh
mios-tamagotchi --sample --save "$HOME/.local/state/mios/tamagotchi-blade-sample/pet.json"
```

The program creates the private save parent directory. The exact same command
resumes the same pet. Each node/user should have its own state; do not share
this save over a network filesystem. The file lock coordinates local processes,
not distributed nodes. `--demo` runs a deterministic hatch/care/memory round,
prints the result and never reads or writes saves.

Normal installed use omits `--sample` and resolves MiOS vendor/host/user TOML
layers. `--root PATH` selects a source/vendor root for development. `--ephemeral`
uses layered configuration but never writes state. Snapshots and redirected
input/output are read-only in every mode, including when `--save` is supplied.
A corrupt save produces an error; it is never silently replaced with a new pet.

## Play

Use arrows or Tab/Shift+Tab to select a care action, then Enter/Space to act.
This works with the existing Inputd terminal key projection; no new global
controller context, physical evdev reader, or subscription socket is added.

| Action | Keyboard | Effect |
|---|---|---|
| Feed | F | Spend one sample coin to restore fullness |
| Memory | G | Watch three arrow directions; repeat them after they disappear |
| Clean | C | Restore hygiene; existing illness still needs medicine |
| Rest | R | Toggle sleep so energy recovers while time runs |
| Medicine | M | Spend three sample coins to heal and treat illness |
| Pause | P | Freeze age, needs and memory countdown |
| Save | S | Save immediately if a save path is active |
| New pet | N | Ask before replacing the current pet; Enter/Y confirms, Escape cancels |
| Quit | Q | Save if persistent, then restore the terminal and exit |
| Back | Escape | Cancel a challenge/reset prompt; otherwise save and exit |

Controller users can reach every action, including pause/save/reset, through
the menu. Memory guesses use the four arrows. Key release events never act. Explicit repeat events only navigate menus; they
do not submit memory guesses or toggle care. Legacy terminal protocols may report
a held key as fresh presses instead of repeat events, so held-button behavior
still needs the real Inputd/terminal checks on each node.
A wrong memory answer costs a little happiness; winning earns coins and restores
happiness. Rest before playing if exhausted. Keep hygiene up and treat illness
to avoid losing health. A dead pet can only be replaced through confirmed reset.

Default sample time is one game tick per two seconds of running play: hatch at
three ticks (about six seconds), child at twenty, adult at sixty (about two
minutes). Needs decay, so reach adulthood by caring and playing rather than
waiting. A thriving adult has health and happiness at least sixty and no illness.
The sample uses original egg/pet art rather than branded character assets.

## Saves and time

Default normal-mode saves live at
`$XDG_STATE_HOME/mios/tamagotchi/pet.json`, or
`$HOME/.local/state/mios/tamagotchi/pet.json` when XDG_STATE_HOME is absent.
The relative directory and autosave interval come from `[tamagotchi]`.
An explicit `--save PATH` can select another per-user/per-node instance.

One process holds `<save>.lock` for its entire interactive session. A competing
writer refuses before entering raw mode. The lock file may remain after exit;
the OS releases its lock when the process exits, including a crash. Never remove
or rotate a lock file while a process owns it. Snapshots may read an active
writer's last completed atomic save without taking its writer lock.

Save schema 1 contains bounded, validated state. Writes use a private temporary
file in the same directory, fsync, atomic replacement, and parent-directory fsync
on Unix. Existing saves survive invalid-state write attempts. Unsupported schema,
malformed JSON, invalid needs/coins/memory progress and files over 64 KiB fail.
Unix saves are mode 0600; new state directories use mode 0700.

Autosave occurs at the configured interval and on Save/Quit. Unflushed progress
may be lost on abrupt termination. Offline time is frozen: loading never applies
wall-clock elapsed time or punishes clock reversal. Pause is saved. The running
clock samples at most one tick per UI iteration, so a suspended process does not
apply a burst of neglect on resume. Save errors return through Ratatui cleanup
and leave the previous completed save available. SIGKILL cannot run terminal
cleanup; use the terminal's normal reset procedure if needed.

## Configuration and rendering

`[tamagotchi]` owns renderer, color, scale, UI tick interval, palette/background,
state_directory and autosave_seconds. `[tamagotchi.game]` owns growth thresholds,
game tick interval, decay/care amounts, costs/rewards and memory reveal time.
The bootstrap configurator follow-up is task_3704. Unknown fields or invalid
values fail before entering raw mode. `--sample` deliberately bypasses live
layers; it is an explicit standalone mode, not a silent fallback.

Glyphs and colors remain independent. Automatic rendering uses half-blocks for
Unicode terminals and ASCII for Linux VT/uncertain locales. Explicit renderer
selection requires a matching font. Linux VT is capped at ANSI colors, VT100/220
at monochrome; nonempty NO_COLOR disables colors. Half-blocks retain two vertical
pixel colors; quadrant/Braille cells share one foreground color. Integer scaling
fits both drawable dimensions after controls are reserved and clips to the buffer.
An oversized fixed scale shows a no-fit message. Tiny panes still accept Quit.

## Blade-agent installation checklist (task_3708)

Claim task_3708 in the canonical MiOS task list and coordinate through native
MiOS-MCP relay/tmux tools. Record a separate result for every real Blade and
user-facing MiOS guest/session tested. No host or delivery endpoint is assumed.
A queued task is not an installation receipt.

1. Pull latest main. Record `git rev-parse HEAD`, node identity, `uname -m` and
   the live MiOS version. Preserve local changes and other agents' lanes.
2. Build inside **MiOS-DEV**, not on the Windows provisioning host. Resolve the
   platform with `miosd native-build-settings --root . --arch x86_64 --json`
   (or `aarch64`). From `tools/native`, build the matching SSOT target:

   ```sh
   cargo build --locked -p mios-tamagotchi --release --target x86_64-unknown-linux-musl
   ```

   For an ARM64 node substitute `aarch64-unknown-linux-musl`. Follow the existing
   native toolchain/linker policy. Validate with `miosd native-artifact-check`
   for the selected architecture, and record SHA-256 of the exact executable.
3. Transfer through the node's already authorized channel and compare checksums.
   On that node, first install the verified executable as a separate user tool:

   ```sh
   install -d -m 0755 "$HOME/.local/bin"
   install -m 0755 /path/to/verified/mios-tamagotchi "$HOME/.local/bin/mios-tamagotchi-sample"
   "$HOME/.local/bin/mios-tamagotchi-sample" --demo
   "$HOME/.local/bin/mios-tamagotchi-sample" --sample
   ```

   `/path/to/verified/mios-tamagotchi` means the verified transferred artifact,
   not an invented remote location. Keep any existing sample executable before
   replacing it. No sudo, bootc switch, daemon, image replacement or live TOML
   replacement is needed for this trial.
4. Hatch the egg, feed/clean/rest/treat, win a memory round and reach a thriving
   adult. Test with an Inputd-owned terminal/tmux pane, verify capture/release,
   and repeat the VT/UTF-8/tiny-pane checks from task_3705.
5. Use the explicit sample save path above. Save, quit and reopen; verify needs,
   coins, age and pause survive, with offline time frozen. Launch a second
   interactive process with that path and verify lock refusal. Try a separate
   corrupt-file fixture and verify it is preserved. Never corrupt the live pet.
6. Pipe `--sample --snapshot` and a persistent `--snapshot`; verify plain ASCII
   output and unchanged saves. Record normal and error terminal restoration.
7. Report per node: source commit, architecture, artifact checksum, install path,
   configuration mode, save path, controller/terminal tested, commands and real
   outcomes. Leave unsupported/unexecuted cases explicit. The image installation
   lane remains task_3703; do not claim the user-local trial updates the OS image.

## Source checks

The crate tests cover core care/growth/death, memory success/failure, controller
menu/reset semantics, independent rendering, clipping, bounded/atomic saves,
local lock refusal, sample/demo isolation and actual CLI read-only/error paths.
Run `cargo test -p mios-tamagotchi` and warning-fatal Clippy from `tools/native`.
Rust 1.89 or newer is required for standard-library file locking. The lockfile
pins Ratatui 0.30.2 and Crossterm 0.29. Local musl/PTY verification does not certify
execution on an ARM64 node, real Inputd ownership, or live image installation.
