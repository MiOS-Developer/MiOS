<!-- AI-hint: Playable local virtual-pet sample, safe save contract and per-Blade installation handoff. -->
<!-- AI-related: tools/native/mios-tamagotchi, usr/share/mios/mios.toml [tamagotchi], task_3707, task_3708 -->
# Playable MiOS Tamagotchi sample

The native `mios-tamagotchi` sample now supports feeding, cleaning, rest,
medicine, illness, death, accelerated growth, a three-arrow memory game,
coin rewards, pause, confirmed pet reset, and versioned local saves.
The goal is a healthy, happy adult. Social play, breeding, shops, multiple
mini-games and full device emulation remain future work under task_3706.

## Graphical room and menus

The interactive view composes the whole terminal viewport: a framed pet room,
window and curtains, floor, furniture, plant, animated pet, care effects, needs
meters, quick actions and modal menus. Wide terminals show a status sidebar;
compact windows keep the scene and care controls readable. Tiny panes retain
Quit. ASCII uses plain borders and characters, while supported Unicode terminals
can use indexed or RGB half-block, quadrant and Braille rendering.

Use **O or F1** for the main menu, **I** for Status, **V** for Graphics and **?**
for Help. Arrows/Tab navigate, Enter/Space chooses, and Escape closes the menu.
Care, Games, Status, Graphics, Help, Save and Quit are working menu entries.
The existing care shortcuts remain unchanged. A menu freezes care time without
adding offline catch-up; ambient animation continues.

Graphics offers separate original **8-bit** and detailed **16-bit-style** sprite
sets, plus renderer choices. Sprite Studio fills the viewport with a preview:
Left/Right changes the reaction, Up/Down changes life stage, and Enter/Space
switches art style. Previewing illness, sadness or death does not alter the pet
or its save. The studio covers the implemented Egg, Baby, Child, Adult and Dead
stages and all implemented expression/reaction clips.

Real care and memory outcomes select bounded eating, cleaning, sleep, medicine,
play and celebration reactions. Hunger, low happiness and illness select their
corresponding persistent moods. Animation uses elapsed time independently of
input traffic and the simulation clock. The animation_frame_ms, reaction_frames,
sprite_style and indexed four- or sixteen-color palette belong to [tamagotchi].
In-menu visual choices last for the current process; they do not replace live
TOML or change schema-1 saved game data.

    mios-tamagotchi --sample --sprite-style eight-bit
    mios-tamagotchi --sample --sprite-style sixteen-bit --renderer half-block --color indexed

A nonempty NO_COLOR remains an explicit request for monochrome output. For a
color trial in a session that inherited it, remove it only for this process:

    env -u NO_COLOR mios-tamagotchi --sample --color indexed

These graphics do not claim the draft's future catalogue of 50+ characters,
additional locations, shops, social systems or multiple mini-games. Those remain
separate gameplay work. Real Linux VT/font and Inputd/controller certification
still require the corresponding device/session checks.

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
layers over the compiled vendor `[tamagotchi]` defaults. Missing or empty user
profiles and older installed profiles inherit newly added settings; explicit
valid choices still win, and malformed or invalid values remain errors. The
Apps and TUIs catalogue exposes the canonical `/usr/bin/mios-tamagotchi` as
**MiOS-Mons** on graphical terminals and Linux consoles.
`--root PATH` selects a source/vendor root for development. `--ephemeral`
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

Save schema 3 contains bounded, validated pet and habitat state; schemas 1 and 2 remain readable. Writes use a private temporary
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
state_directory, autosave_seconds, sprite_style, animation_frame_ms and reaction_frames. `[tamagotchi.game]` owns growth thresholds,
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

### Focused controller prompts

The game reads the native Inputd map and connected-device ownership in a bounded
background probe. A controller hint is shown only when a connected controller
belongs to this session and the compositor's focused terminal client has this
game's pane selected. Detach, foreign focus, unreadable or stale evidence restores
keyboard hints. Snapshot and demo commands never start controller probes.

The quick-care dock, menus, sprite studio, memory answers and new-pet prompt use
the resolved positional buttons and controller-family labels. With a focused
controller, the mapped Back button opens the main menu from the room; choose
Quit there. Keyboard Q still saves and quits. A controller's system buttons keep
their native Inputd behavior: Start is not advertised as Pause, and a desktop
stick that scrolls is not advertised as directional game input.

Controller hints are presentation only. The app consumes the existing terminal
key projection, never grabs evdev devices or changes desktop input ownership.
The polling, timeout, stale-age and output limits are vendor settings under
[tamagotchi]; graphics and menu settings continue to use the embedded sample
configuration for the user-local trial.

### Autonomous voxel view and compact HUD

The default view is a CPU-rendered 3D voxel MiOS-Mon. Its volumetric body, face,
limbs and care props rotate with depth occlusion. The classic 8-bit and detailed
16-bit pixel views remain selectable. Graphics also chooses Auto, Full or Minimal
HUD; CLI overrides are --view voxel|pixel and --hud auto|full|minimal.

Pet walking and turning are autonomous, using a separate seeded visual RNG and
active simulation decisions conditioned on life stage, health, energy and sleep.
Eggs and dead pets do not walk. Pause, menus, memory challenges and care reactions
stop locomotion. Rendering, controller navigation and repaint frequency never
consume the saved gameplay RNG or manufacture age, needs, coins or memory wins.
The controller operates menus and HUD actions only; it never steers the pet.

Minimal HUD keeps the pet, stage/state, fullness (F), happiness (H), energy (E),
health (+) and selected care action. Other needs are in Status. Auto switches
below the configured width/height thresholds. At 20x8 or larger every menu choice
scrolls into view, and new-pet confirmation stays visible. Smaller viewports retain
safe quit. This cell-based layout works through console and remote-terminal
transports, including terminal-based smartwatch webviews; it does not add a new
browser server. Headless snapshots remain plain ASCII and do not require a GPU,
display server, controller, writable save or animation clock.

### Sixteen animal MiOS-Mons and dialogue

The embedded catalogue contains Mio, Yuzu, Mugi, Tofu, Kumo, Nori, Sora, Raku,
Suzu, Aki, Hinoki, Haru, Mochi, Puku, Kiri and Iwa. They are original cat, fox,
tanuki, rabbit, bear, frog, bird and turtle characters with distinct volumetric
variants, palettes, hobbies and voices. Eight gender-neutral personality
archetypes each have two individuals. These are Japanese-inspired original
characters, not Animal Crossing character assets or copied dialogue.

The main menu includes Talk and Roster. T opens Talk; B opens Roster. Roster is a
read-only preview until adoption is confirmed. Arrows/controller navigation
select menus and HUD actions; they never steer or rotate the pet. A confirmed
adoption creates a new egg and resets presentation position and animation state.

Thoughts and dialogue appear in a pane below the pet. In a focused, uniquely
attached native tmux client, Talk can open a floating popup; right-click opens a
native tmux context menu. The game enables mouse reporting only for its own
terminal lifetime and restores it on normal/error exits. Native callbacks return
only a bounded private choice; the main process retains the single save writer.
There is no broadcast to another pane or default/headless tmux server.

Use --dialogs inline to keep all conversation inline. Small, restricted,
unattached, ambiguous/multiple-client and failed native-popup contexts use the
inline pane. Read-more paging keeps longer conversation accessible in compact
views. Snapshots print the full current thought and speech as plain ASCII.

New saves use schema 3 to retain stable character identity and a seeded habitat. Schema-1 files load
without modification, preserve all original gameplay state, and receive the
default Mio identity; an explicit interactive save writes schema 3 atomically.
Older executables cannot read schema 3, so retain the pre-upgrade binary and save
backup for rollback. Unsupported versions, invalid identities and malformed
catalogues are refused before raw mode. A read-only snapshot never upgrades a
save.

Research separates Nintendo's animal-role design discussion from community
documentation of personality pools, hobbies/subtypes and species-first
selection. See [the research note](research/mios-tamagotchi-villager-model.md).
[Nintendo's developer interview](https://iwataasks.nintendo.com/interviews/3ds/animalcrossing-newleaf/0/2/),
[Nookipedia's villager model](https://nookipedia.com/wiki/Villager) and
[mystery-tour selection](https://nookipedia.com/wiki/Mystery_Island) informed the
original implementation.


## Mature bodies, ASCII faces and themed habitats

Child and adult pixel art uses double-height canvases: 8x16 in the eight-bit
style and 16x32 in the detailed style. Their voxel canvases are 24x48 and 32x64,
with actual separate heads, torsos, arms and legs. Egg, baby and ghost forms
remain compact. All sixteen original animal identities retain their shapes,
palettes, personalities, hobbies and state-aware original dialogue.

ASCII kaomoji faces blink and change with care and mood. Literal facial text is
drawn on a sufficiently resolved front-facing head; side/rear faces are hidden
by direction and depth. Tiny views retain a short face in the stage HUD when
the sprite cannot hold the complete expression. The same clipped renderer is
used for half-block, quadrant, braille and plain ASCII output.

Each new pet receives a cosmetic room seed and initial theme derived from its
creation seed, independently of the saved memory-game RNG. Eight vendor themes
provide different wall/floor palettes, patterns and seeded window/furniture
placement. The room remains stable across redraws, pause and save/reload.

Open **Habitat / items** from the menu or native right-click menu, or press
lowercase **h**. Items show an explicit coin price, earned threshold or owned
placement action. Enter/Space buys and places an affordable item, or toggles
an owned/earned decoration. Arrows/Tab navigate and Escape returns. Controller
hints use the actual focused mapping. These controls decorate the habitat;
they never steer the pet.

The vendor catalogue contains sixteen items, including affordable plants and
seats and rewards unlocked by actual memory wins or age progression. Earned
eligibility is separate from permanent purchases. Failed/duplicate purchases
never consume coins, and only owned/earned items can be placed. A room slot
holds one decoration. Theme changes are explicit and free; there is no passive
or automatic spending.

Names, prices, reward thresholds, palette indices, item kinds and theme patterns
come from the layered [tamagotchi.habitat] SSOT. Older live settings inherit the
embedded vendor tier, without replacing the operator's TOML. Custom catalogues
are validated before raw mode and used by active save/load operations.

Schema 3 stores seed, theme, purchased inventory and placed items. Legacy 1/2
saves gain a deterministic habitat in memory while retaining their original
pet fields, currency and gameplay RNG. A redirected snapshot reports the room
and placed item names in plain ASCII without writing or applying offline time.
Keep a backup before an interactive migration; older binaries cannot load schema 3.

Native focused Talk/thought popups and right-click windows keep the same
verified-client contract. Tiny, unfocused, restricted or multiply attached
clients use the paged inline dialogue pane below the pet. Actual phone and
physical-controller acceptance is recorded per session; compatibility does not
by itself certify a device.

## Closer cameras and cell-budgeted HUD

The Room, Pet and Face camera views use actual solid sprite bounds. The default
Pet framing removes transparent padding; Face focuses on the head. Zoom crops
rather than silently fitting the pet back down. Press + or = to zoom in, - to
zoom out,0 to reset and lowercase z to cycle views. The mouse wheel and focused
controller Graphics menu provide the same presentation actions; none steers the
pet. --camera room|pet|face and --zoom50..400 select initial framing.

Zoom and camera changes use bounded smooth interpolation. The layered
[tamagotchi] zoom_percent, zoom_step_percent and zoom_transition_frames settings
control their initial value, step and transition length.

The compact HUD puts identity, stage, expression and needs into the viewport
frame. Its landscape sidebar and portrait stack derive proportions from the
existing [mcp.tmux.workspace] golden_ratio, resource_min_columns and
portrait_ratio_percent SSOT fields. Tiny frames page needs; *=100 exactly.
At smartwatch-sized12x6 clients the default Pet view adapts to a frontal Face
view, with short selected-action and Menu prompts. Native tmux popups are used
only when the verified focused client fits; smaller clients use paged inline
menus/dialogue. Reset requires a readable confirmation and cancels after a
resize that hides it.

All living moods have pure ASCII expression cycles. Detailed voxel rendering
re-samples the model at a finer lattice rather than enlarging block pixels.
Frontal kaomoji appears on the head; oblique views show only the nearer half,
and rear views do not receive face text. The HUD can still report mood.

Inputd-projected arrows, Enter, Tab and Back follow the same menu/care actions
in every terminal transport. Pad-specific hints require native focus and device
ownership proof; absent graphical context cannot certify console or remote
controller ownership. Physical device acceptance remains separate.
