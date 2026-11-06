<!-- AI-hint: Playable local virtual-pet sample, safe save contract and per-Blade installation handoff. -->
<!-- AI-related: tools/native/mios-tamagotchi, usr/share/mios/mios.toml [tamagotchi], task_3707, task_3708 -->
# Playable MiOS Tamagotchi sample

The native `mios-tamagotchi` sample now supports feeding, cleaning, rest,
medicine, illness, death, accelerated growth, a three-arrow memory game,
coin rewards, pause, confirmed pet reset, and versioned local saves.
The goal is a healthy, happy adult. The sample includes original character
profiles, dialogue, seeded habitats, decoration purchases and training. Social
play, breeding, multiple mini-games and full device emulation remain future
work under task_3706.

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
TOML or change saved pet state.

    mios-tamagotchi --sample --sprite-style eight-bit
    mios-tamagotchi --sample --sprite-style sixteen-bit --renderer half-block --color indexed

A nonempty NO_COLOR remains an explicit request for monochrome output. For a
color trial in a session that inherited it, remove it only for this process:

    env -u NO_COLOR mios-tamagotchi --sample --color indexed

The sample includes sixteen original character profiles and eight seeded room
themes. The draft's larger catalogue, additional locations, social systems and
multiple mini-games remain separate gameplay work. Real Linux VT/font and Inputd/controller certification
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

Save schema 4 contains bounded, validated pet, habitat and hatch/training state; schemas 1 through 3 remain readable. Writes use a private temporary
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

New saves use schema 4 to retain identity, habitat and hatch/training progression.
Schemas 1..3 load without modification and preserve original gameplay fields;
schema 1 retains the default Mio identity. An explicit interactive save writes
schema 4 atomically. Older executables cannot read schema 4, so retain the
pre-upgrade binary and save
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
style and 16x32 in the detailed style. Their voxel canvases are 24x48 and 64x128,
with actual separate heads, torsos, arms and legs. Egg, baby and ghost forms
remain compact. All sixteen original animal identities retain their shapes,
palettes, personalities, hobbies and state-aware original dialogue.

ASCII kaomoji faces blink and change with care and mood. A code-native stroke
atlas is sampled through the actual visible head surface: the expression scales,
foreshortens and rotates with the model. Species noses, ears and props remain
solid geometry and occlude the texture through the same depth buffer. Atlas
gaps preserve the actual species/head material. No second
set of voxel eyes or mouth competes with the expression. Side/rear views cannot
paint a floating frontal badge. A thin, spaced atlas supplies larger heads. Underresolved heads use individual
terminal glyphs anchored to visible head-local UVs; each complete glyph cell must
still pass the actual material/depth mask. Tiny scenes, legacy pixel sprites and
read-only snapshots retain literal facial text. Compact HUDs keep a separate
mood caption. The same clipped projection is used for
half-block, quadrant, braille and plain ASCII output.

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
Keep a backup before an interactive migration; current interactive saves use schema 4.

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
pet. --camera room|pet|face and --zoom 50..400 select initial framing.

Zoom and camera changes use bounded smooth interpolation. The layered
[tamagotchi] zoom_percent, zoom_step_percent and zoom_transition_frames settings
control their initial value, step and transition length.

The compact HUD puts identity, stage, expression and needs into the viewport
frame. Its landscape sidebar and portrait stack derive proportions from the
existing [mcp.tmux.workspace] golden_ratio, resource_min_columns and
portrait_ratio_percent SSOT fields. Tiny frames page needs; *=100 exactly.
At smartwatch-sized 12x6 clients, Pet and Room framing adapt to a frontal
Face view, with short selected-action and Menu prompts. Unhatched eggs retain
whole-pet framing because they have no face surface. The selected larger view
returns when the client grows. Native tmux popups are used
only when the verified focused client fits; smaller clients use paged inline
menus/dialogue. Reset requires a readable confirmation and cancels after a
resize that hides it.

All living moods have pure ASCII expression cycles. Detailed voxel rendering
re-samples the model at a finer lattice rather than enlarging block pixels.
Kaomoji is mapped to head-local coordinates on the visible front surface.
Oblique views reveal the geometrically visible portion through foreshortening
and depth occlusion; rear views receive no face texture. The HUD can still
report mood.

Inputd-projected arrows, Enter, Tab and Back follow the same menu/care actions
in every terminal transport. Pad-specific hints require native focus and device
ownership proof; absent graphical context cannot certify console or remote
controller ownership. Physical device acceptance remains separate.


## Hatch identity and training

New pets roll an original generated name, five initial stat values and innate
aptitudes when the egg actually hatches. Species selects the primary palette;
the current personality archetype selects a distinct secondary palette.
The sixteen catalogue entries remain original species/voice profiles and
adoption previews; a generated pet name is separate from its profile name.
Legacy pets retain their existing names, needs, age, coins, wins and memory RNG.
Their added stats are initialized deterministically in memory without offline
progress or an automatic save rewrite.

Press lowercase **l**, choose **Training / stats** in the main menu, or choose
the focused native context action. Swim, Flight, Run, Power and Stamina have
separate values, levels, XP and innate grades E..A. A successful session spends
food and energy and gains XP based on the configured range plus aptitude.
Earned levels increase actual stat values and grant the configured coin reward.
XP leftovers are preserved. Exit the menu for active game time before another
session; menus and pause freeze the cooldown. Egg/dead/paused/sleeping/ill pets,
active memory games, insufficient needs, level caps and cooldowns refuse
without changing either RNG or charging resources.

The layered [tamagotchi.game.training] table owns name syllables, family and
personality palette indices, hatch rolls, XP thresholds/range, level cap, care
costs, cooldown and rewards. The MiOS configurator exposes these controls and
the camera/HUD options; saving an overlay does not replace live system settings.
Schema 4 strictly stores progression seeds, name, stats and counters. Supplied
malformed/future counters are rejected in legacy and current envelopes. A
redirected snapshot reports all five skills as plain ASCII, remains read-only
and can run while an interactive writer holds the pet lock.

## Landscape, portrait and smallest views

Landscape viewports use the inherited golden-ratio scene/sidebar split;
portrait viewports stack information and dialogue below the pet. Resizing keeps
the chosen zoom and view settings. Automatic Pet/Face changes blend through
intermediate camera frames using the same transition budget as explicit zoom.
Frame titles and paged compact status keep needs, age, coins, wins and skill
levels available. Status and Help wrap before paging so all content remains
reachable. Full HUD also inherits the workspace proportions.

At 12x6 terminal cells, Roster and Sprite Studio reserve an actual face scene,
and Training/Habitat keep a selected choice plus explicit accept/back prompts.
Dialogue clears its own opaque area below the pet and pages long thoughts.
H- in a compact controller label means Hold; full mapped controls and release
chords are available in paginated Help. Controllers operate menus, care and
camera choices, while the pet's locomotion remains autonomous.

Smartwatch and mobile acceptance requires a real attached device. Testing
terminal cell dimensions establishes layout behavior; it does not certify an
unattached phone/watch, unavailable font, VT renderer or physical controller.

## Doll anatomy and surface expressions

The animal model uses a broad head, one short torso, small attached paws and
stub limbs. Child/adult canvases remain double height; the anatomy is not
stretched to fill them. Species identity comes from ears, muzzle, actual nose
or beak and relevant tail or shell. Sixteen original variants cover cats, foxes,
tanuki, rabbits, bears, frogs, birds and turtles. Baby scaling preserves the
compact proportions. Eggs remain closed neutral shells before hatching.

The doll geometry and face atlas are code-native CPU rendering data. Head UVs
are recovered from the winning depth-tested surface, rather than a screen-space
label. Pure ASCII eyes and mouth animate as a single expression; the nose is
species geometry. Readable portrait information panels use their available
height for needs, currency and all five training skills before paging thoughts.
Camera projection corrects standard 1:2 terminal-cell proportions for ASCII
and quadrant packing; half-block and Braille retain square packed pixels.
Fonts with other metrics still require actual terminal review. Actual PiP, font,
console and mobile results remain separately evidenced.


## Controller ownership across nested sessions

Controller routing uses the native per-seat Inputd contract. A labelled nested
session receives only the devices declared by its launcher; another live seat
cannot claim an exclusively assigned node. Each seat has its own compositor,
Tmux-OS namespace and room packet. A host-side `mios-inputd bridge --nested SEAT`
follows that live registry entry and routes its assigned controllers. The parent
bridge excludes assigned nodes. Multiple seats can therefore use different
controllers concurrently; controller count is not a global game setting.

A session launched with `--no-devices` has no controller assignment. A physical
pad detected on the parent desktop does not establish input ownership in that
session. Existing launcher device namespaces cannot be expanded merely by editing
a descriptor. Preserve a running operator session and use the documented launch
and assignment workflow for a future device-enabled session.

ServerBox SSH forwards terminal input. A controller connected to a phone must
actually produce forwarded terminal events before its controls can be verified;
server-side device discovery alone cannot identify that phone controller.
Controller actions remain menu, care and HUD navigation. Pet locomotion is driven
by the simulation. Per-seat routing, physical devices, remote terminals and
concurrent player acceptance are recorded separately.


## Direct navigation and compact control legends

Letter shortcuts accept the displayed capital or its lowercase equivalent.
Arrows/Tab choose care actions; Enter/Space activates the selected action.
F/G/C/R/M address feed, memory, clean, rest and medicine. P pauses or resumes,
S saves, O opens the menu and ? opens paginated controls. Q remains the explicit
quit shortcut. Escape backs out of nested menus, cancels memory or opens the
home menu from ordinary play. It does not quit ordinary play.

Numbered visible menu rows accept 1 through 9; 0 retains camera zoom reset.
Mouse menu rows are clickable, and wheel events navigate an open menu instead
of zooming the room beneath it. Small views expose resume/menu/help controls;
larger compact views show direct care and camera bindings. Inputd diagnostics
explain missing assignment, unavailable context, stale evidence or focus loss
separately from verified controller prompts. A diagnostic never grants ownership.


## Expressive faces and voxel lighting

The sixteen existing mood and care-reaction states each have eight animation
poses. Living states have at least four distinct faces; death remains still.
The catalogue contains 59 distinct five-character ASCII expressions, including
chewing, glances, closed eyes, tears, pouts and excited faces. W-shaped mouths,
3-shaped pouts and Q-shaped tears have native-text and voxel-atlas glyph support.
Compact heads may omit both outer parentheses when only the eye-mouth-eye core
fits. A lone parenthesis or missing mouth is not a complete compact expression.

Voxel lighting reads the normal and contact occlusion of the winning visible
surface. Hidden faces cannot replace its lighting, and camera/portrait resampling
uses the same source pixel for material, face mapping and shading. Species and
personality palette indices remain unchanged. The renderer shades their actual
colors, and ASCII views use a tonal character ramp; monochrome retains its
uncolored output. Facial ink stays separate from body illumination.
Silhouettes use visible screen edges as well as model edges, retaining contrast
against the room at larger zooms. Magnified, shaded body surfaces gain sparse
ASCII texture; face glyphs, occluders and room cells retain their own rendering.

The layered [tamagotchi.voxel_lighting] table and MiOS configurator control the
ambient and directional light, contact shade, silhouette edge shade, view-space
light direction and ASCII ramp. These are local application rendering settings;
changing a preview does not replace the running host's configuration or mutate
pet needs, age, coins, progression or RNG.


## Global detail when fonts or viewports change

Automatic detail uses one shared 8-bit/16-bit decision for the room, pet, face
and character previews. It responds to the actual terminal cell grid, renderer
packing and camera zoom. Enlarging the terminal font reduces the available
cells and selects coarse detail; restoring space selects finer detail. The HUD
and menus reflow within that same viewport and show the effective mode.
Frame headers use A8/A16 for automatic detail and M8/M16 for manual detail,
reserving space for the pet name, stage, pause/death and illness indicators.
At 12 cells wide, the stage is abbreviated and the detail badge appears once.

The layered [tamagotchi.presentation] table owns automatic selection, the
minimum packed-pixel dimensions and resize hysteresis. Graphics offers manual
8-bit/16-bit choices and a return to Automatic. An explicit --sprite-style
choice selects manual detail. Terminal font size is a client-wide setting, so
changing it scales every ASCII label and facial character together.

When a restricted scene cannot fit a complete expression on visible head cells,
it reserves a plain expression row below the cropped sprite. This inline fallback
never grants permission to draw over a beak or occluder, and eggs remain faceless.


## Browser viewport and user-local page trials

The generated page keeps the hidden xterm IME textarea keyboard-focusable while
excluding it from pointer hit-testing. Chromium touch adjustment otherwise snaps
some footer taps onto that invisible textarea beside the terminal cursor and
emits a different SGR row. The narrow textarea rule leaves the visible terminal
interactive; no game or tmux coordinate offset compensates for this browser issue.
Browser receipts distinguish an injected diagnostic rule from the regenerated,
served production page, and physical phone acceptance remains separate.

The native ttyd page is generated from the pinned [ttyd] upstream source and
validated against its projection golden. It declares a device-width viewport so
phone-sized browsers resize terminal cells instead of shrinking a desktop layout.
Browser zoom remains available. This page is shared by native terminal variants;
its launcher does not imply that a terminal variant runs in a container.

For a separate user-local trial, MIOS_TTYD_PAGE_PATH selects a readable generated
page without replacing the host page or live TOML. An explicitly selected missing
page fails before launching ttyd. The default continues to resolve [ttyd].page_path.
Retain the source pin, projected page checksum, listener identity and actual
authenticated terminal interaction as distinct receipts. Loopback URLs on a
phone refer to that phone; remote play uses an existing authenticated access
route to the MiOS host. A local browser simulation does not certify a phone,
physical controller, PowerShell runtime or another Blade.


## Make a live user-local trial available in the native menu

A binary installed under ~/.local/bin is not automatically the executable of
the existing MiOS-Mons menu row. Inspect the normal user's mios-gen tui-spec
mios-mons --root / --console 0 output and the actual native launcher before
claiming that a live deployment is available from tmux-os.

A temporary user fragment under ~/.config/mios/mios.d may redirect the existing
terminal.tuis row to the verified user-local executable with --sample and a
separate explicit menu-play save. Array layers replace the whole TUI list:
preserve every other resolved row and its options, and retain the prior list
for comparison. Do not replace the host monolith to install one application.
Remove only that owned temporary fragment after normal image integration.

The native launcher reads this catalogue at open time. Open Ctrl+B then Space,
find MiOS-Mon under MiOS, and select the actual row. Verify the resulting process
checksum, save path and visible game in the caller's own runtime. End a disposable
menu verification run normally so it does not hold the operator's menu-play
save lock; preserve existing live pets and nested launchers. An already running
pet is resumed through its running window; do not remove its writer lock.

## Portable development identities and voxel rooms

Development play is save-free with `mios-tamagotchi --sample`. A fresh nonzero
64-bit seed generates species, name, personality, species-bound main colour,
personality accent, trait, trope, likes/dislikes, habitat and hatch statistics.
Press **I** (or Home / Birth code) for its compact 17-character `M1` code.
The panel keeps the complete code visible at 20x8. Copy it as ordinary ASCII
over SSH, web terminals or text messages, then replay with `--sample --seed CODE`.
Decimal seeds also work: `--sample --seed 123456789 --snapshot`.

The version-one code losslessly encodes the seed with a copying-error checksum;
it is not a digest of a save, a secret or a container for trained progress.
Reproduction requires matching generation settings (embedded `--sample` provides
the pinned vendor rules). **S** opens the code panel during save-free play.
Optional `--save-dir DIR --seed CODE` stores mutable progress as
`DIR/COMPACT_CODE.json`; explicit `--save PATH` is still supported. A supplied
seed must match an existing save. Legacy pets with unknown original seeds are
preserved and do not receive an invented reproduction code.

Hatch preview values are labelled previews. They become actual statistics only
when the simulation hatches the egg. One seed-selected skill receives the
configured hatch bonus; normal training still consumes care resources and XP.
Reading or sharing an identity never advances age or either gameplay RNG.

The default Room camera uses a depth-rendered tiled voxel floor, cutaway walls
and solid placed furnishings. Its elevation stays at 30 degrees; **[** and **]**
orbit the world with a smooth shortest-path transition. Graphics offers orbit
and reset controls. The camera follows the Mon's autonomous movement. Pet and
Face close-ups remain available, along with ASCII/8-bit/tiny-terminal fallbacks.
World size, follow, orbit, identity vocabulary and hatch bonus are configured
in vendor TOML and the configurator; no live TOML replacement is required.

### Verified live seeded-world trial

The user-local x86_64 trial from source f54b6b57ac5c5f40d030bf34cb2e28733a03efe9 passed482 scoped
MiOS-DEV checks,warning-fatal Clippy and native static PIE validation. Actual
nested/browser tests covered the rotating isometric room,short code replay,
portrait/landscape and a 20x8 code/menu. Automated live care and visible memory
answers reached a thriving adult without editing its age,needs,coins or stats.
The normal user's MiOS-Mon menu row launches --sample without a save.

Receipt: /home/mios/.local/state/mios/mios-mons-world-live/mios-world-handoff.json.
This remains a development trial; physical controller/phone/VT/ARM64/fleet and
OS-image acceptance are separate. Existing pet files are preserved.

## Replay a shared birth code inside the game

Use **K** or **Home / Import birth code** during save-free development play.
Type a decimal seed or paste a compact/grouped M1 birth code. The editor accepts
at most 21 ASCII characters; invalid checksums and control-bearing clipboard data
are refused without changing the current pet. **Q/F/P/N** and other code letters
are text while this field is focused. **Ctrl+C** still quits; **Escape** returns.

**Enter** checks the code and opens a separate confirmation showing the complete
compact code and generated name. **Enter** again creates that exact birth as a
paused egg; **Escape** keeps the current pet. **P** resumes after confirmation.
Menus freeze the simulation clock, so typing and previewing do not advance age
or needs. This reproduces birth identity and hatch stats, not trained progress.

For controller menu operation, Left/Right selects a payload character and
Up/Down cycles its Base32 value. Accept checks/starts; Back keeps the pet. The
onscreen hints use the current focused controller mappings. Mouse clicks or web
terminal taps position the caret and activate Check/Keep. The visible [<]/[>]
buttons move the caret, [-]/[+] cycle a character, and [Clear] (or [Clr] on a
watch grid) clears the draft. Ctrl+U also clears the focused field. These controls
fit 20x8, edit only the draft, and disappear behind confirmation or when the
viewport cannot paint them. Software routing is tested separately from physical
controller ownership.

The complete compact code and both confirmation actions fit a 20x8 terminal.
Smaller views refuse confirmation; the tiny editor shows Escape/Back and Ctrl+C,
rather than claiming that Q quits while it is an editable code character.
Clipboard paste outside the focused editor is ignored. Bracketed paste and
mouse capture are restored on normal exit.

This development import is disabled for any session with a save path. Existing
saved pets and seed-named progress files remain separate and cannot be replaced
through this menu. Use --sample without --save or --save-dir to import a code.

After an authorized main push, install the verified user-local sample live and
verify its running preview against the published source. PR-branch pushes do
not trigger this installation. Preserve adopted pet sessions; use an owned
fresh preview rather than replacing their process. Retain a post-push receipt
with the main revision, artifact source/checksum, installed path and live target.

Earlier trials used status-off and smallest-client sizing as workarounds.
The verified development game now uses per-window latest sizing so the focused
client can fill its own portrait, landscape or watch viewport. The native status
bar remains available; production touch checks cover top, bottom and absent
status without a diagnostic page injection.
Investigation of the earlier top-status click miss found a client-fixture
coordinate error: a pane capture omits status rows and split-pane offsets,
while a terminal client's SGR coordinates include them. The native tmux
transport subtracts those offsets exactly once. The application consumes the
resulting pane-local coordinates without another subtraction.

The task3747 MiOS-DEV probe measures independently painted client cells and
raw input at both ends across top/bottom/off status, one/two/five status rows,
and single/split panes. Its planted omitted-status control reproduces the
one-row miss. Browser automation must locate the actual client-buffer cell
or add the real pane/status offsets before converting it to screen pixels.
Historical trial receipts are retained. Automated terminal/browser evidence
does not certify a physical phone or controller.

A separate application race occurs when multiple clients use tmux's latest
window size: a click can arrive before the corresponding resize event. Before
hit-testing a left click, the game compares the current terminal size with its
last painted frame and redraws if needed. This rebuilds targets for the tapping
client's size without changing mouse coordinates or applying a second offset.
Browser checks also retain actual emitted SGR bytes and reject a client refit
that invalidates a previously measured touch position.

## Complete menu touch and cursor navigation

At 20x8 and larger, [<] and [>] select the previous and next menu option or
scroll an information page. The painted accept control activates only the
selected option; Back returns without buying, training or replacing a pet.
The sprite studio also paints [^] and [v] for life-stage previews. These inputs
remain menu operations; pet movement stays autonomous.

Page Up/Down pages list selections or information text. Home/End selects the
first/last list option or information page. Seed entry keeps its own caret
Home/End behavior. Cursor keys and SGR mouse input use the same handlers in
local terminals, SSH/tmux, browser terminals and headless PTYs; a redirected
noninteractive invocation remains a read-only snapshot.

When multiple verified controllers share the focused owned session, prompts
retain each eligible family's actual resolved binding, such as Sony: Tap Cross /
Xbox: Tap A. Identical actions are deduplicated. Tiny captions compact known
family prefixes and button names while preserving Hold semantics. Unknown
devices keep generic cardinal labels. Foreign/unassigned controllers do not
contribute prompts. This does not certify physical phone-controller forwarding.


## Console HUD and full-frame menus

Every menu uses a viewport-wide text shell with its title, navigation history,
visible selection or information-line range, and a fixed two-row control area.
Care, Games, Talk, Roster, Status, Graphics, Help, Sprite studio, Habitat,
Training, Birth code and the code editor remain reachable from Home. Moving
through a list or resizing it keeps the selected row on the visible numbered
page. Page Up/Down, Home/End, arrows and Tab navigate; Enter/Space accepts and
Escape returns through the menu history. The editor retains its own text and
caret controls. Quit and reset remain explicit actions.

The same painted Previous/Next/Accept/Back controls accept terminal mouse
events and browser taps. Hitboxes are rebuilt from visible cells, discarded
when a different menu or native modal owns input, and cleared after activation
until the next repaint. Closing a menu cannot activate the care button behind
it. Native floating dialogs keep the underlying pet view; inline text is the
fallback on consoles, SSH, remote terminals and headless PTYs.

Ordinary play retains Auto/Minimal/Full HUD modes, golden-ratio scene/status
splits, pet identity and life stage, needs, health, illness/pause state and
currency within the current cell budget. Compact views page complete metrics
rather than inventing clipped values. Essential menu controls remain static.
Verified focused controller bindings use their actual family and remaps;
keyboard controls and Help remain available when no eligible controller is
focused. Controller navigation does not move the Mon.

The supported compact menu floor is 20x8 cells. Portrait and landscape resize
through the same geometry. Below the full interactive floor, the emergency
display preserves quit/back and protects unfinished confirmations. For an
ASCII-only monochrome terminal, run:

~~~sh
mios-tamagotchi --sample --renderer ascii --color mono --dialogs inline
~~~

Use the already attached authorized VT/console, SSH terminal, tmux pane or
web terminal; basic menus need no desktop compositor or graphics service.
A pipe or terminal without interactive input produces a read-only snapshot.
Physical VT fonts, touch devices and remote controller forwarding still need
their own actual device/session acceptance.

### Three camera perspectives

**Z** cycles Room, Mon and Face close-up. Graphics exposes the same choices.
**+/-** zoom, **0** resets zoom, and **[ / ]** orbit. Room uses the locked
30-degree isometric elevation and follows autonomous Mon movement. Mon frames
the body. Face close-up fits the actual visible mapped head, keeping ears
without letting a wide tail or long torso shrink the face.

Entering Face turns the camera toward the Mon's front; subsequent orbit input
rotates relative to that entry direction. Side/rear views preserve real
surface occlusion. Returning to Mon or Room blends back to the world-oriented
perspective. Camera, HUD and menu operations remain separate from simulation,
birth identity, trained statistics and progress saves.

The current simulation follows one local Mon. Large-room and multiplayer
transport work must preserve this separation and the existing per-seat Inputd
ownership contract; the camera controls do not establish network play.


## Menu descriptions and constrained terminals

Home, Care, Games and Graphics show a description when the frame has spare
rows. Descriptions occupy only unused space above the controls, so adding one
does not move a menu choice or create another touch target. Saved pets describe
Quit as saving progress and keep birth-code import protected. Monochrome menus
use reverse-video selection; colored menus select contrasting foreground and
background colors from the active terminal palette.
