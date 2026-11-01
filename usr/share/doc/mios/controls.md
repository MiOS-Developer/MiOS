<!-- AI-hint: Operator guide to mios-controls, the terminal binding editor over the layered binding map: keyboard, mouse and controller drawings (VT-safe), every use of an input across the global map, compositor tables, tmux-os, the Guide layer and the Hypr3D room, rebind / capture-to-bind / search / conflicts / undo, keyboard + mouse + controller navigation, the draft -> validate -> apply workflow and its headless verbs. -->
<!-- AI-related: tools/native/mios-controls/src/main.rs, tools/native/mios-controls/src/tui.rs, tools/native/mios-controls/src/layout.rs, tools/native/mios-controls/src/pads.rs, usr/share/mios/mios.toml [desktop.controls], usr/share/doc/mios/keybinds.md, tests/test-mios-controls.py, tests/test-mios-controls-visual-live.py -->

# MiOS Controls

`mios-controls` shows and edits the MiOS binding map from a terminal. It runs
anywhere a terminal does: a desktop terminal, a tmux-os pane, a Linux VT
(`TERM=linux`), SSH or ttyd. It draws your keyboards, mice and controllers,
shows everything each key or button does, and changes bindings as a draft of
your user tier (`~/.config/mios/mios.toml`). A draft is checked before it can
be written. There is no second binding store.

## Views

`1`-`5` (or `<` `>`, or a controller's triggers) pick a view.

| View | Shows |
|---|---|
| 1 Keyboard | a full keyboard: function row, main block, navigation cluster, arrows and number pad, drawn as boxed keys |
| 2 Mouse | left, middle and right buttons, the wheel (up, down, tilt left and right), the side buttons (Side, Extra, Forward, Back, Task) |
| 3 Controller | face buttons, d-pad, both sticks (directions and clicks), triggers, shoulders, Guide, Start and Back. Glyphs follow the pad's family: Xbox (A B X Y, LB RB LT RT, View, Menu), PlayStation (the shape symbols, L1 R1 L2 R2, Create, Options, PS), Nintendo, or generic |
| 4 Bindings | the global map as a table: `!` marks a finding, `*` a draft change; the `here` column follows the context picked with `[` and `]` |
| 5 Devices | the connected keyboards, mice and controllers with their family and buttons. Enter opens that device's drawing; inputs the device does not have are dimmed |

The layer line above the drawing narrows it to one layer (`l` / `L`):

- keyboard: no modifier, each modifier combination in use (SUPER,
  SUPER+SHIFT, ...), and the tmux-os prefix layers (`CTRL+b then ...`)
- mouse: the modifier layers that bind mouse buttons, and the tmux-os
  pane and status menus
- controller: the desktop, Guide held, the Hypr3D room, and a captured app

Inputs bound on the layer are highlighted. The layer's held modifiers and
prefix key are marked, conflicts are red, and the cursor is reversed.

On a Linux VT (`TERM=linux`), or with `[desktop.controls].glyphs = "ascii"`,
the drawings use single box lines and ASCII labels that the VT font has
(PlayStation shapes become X, O, Sq, Tri). `glyphs = "ascii"` also replaces
the box lines with `+ - |`.

## Every use of an input

The panel beside the drawing lists everything the selected input does, on
every layer:

| Source | Where it lives | Editable here |
|---|---|---|
| global | `[keybindings.global].bindings` | yes, unless the compositor session's own bind realizes it |
| escape | the `bypass_mod` escape chord of a global binding | through the global binding |
| desktop, screenshot, shaderglass | `[desktop.hyprland]`, `[desktop.screenshot]`, `[desktop.shaderglass]` `bindings` rows | yes. A row that realizes a global binding moves together with it |
| tmux-os | `[keybindings.tmux].bindings` (after the prefix) | yes |
| guide | `[keybindings.global.controller.system_layer]` (Guide held + button) | yes. Buttons swap |
| room | `[keybindings.global.controller.room]` | yes |
| fixed (`-`) | built-in roles: Guide, take/return control, cursor mode, room movement, terminal-reserved chords | no. The panel shows where they are set |

## Changing a binding

| Key | Action |
|---|---|
| Enter | open the selected input's uses (or the selected use) |
| r | rebind the selected use. Move to the new input and press Enter, or press `c` and then the physical input |
| c | capture: press a key, mouse button or wheel, controller button, trigger or stick. That input is selected on its drawing. Esc cancels. It stops by itself after `capture_timeout_ms` |
| a | change the action (pick from the known actions or type one) |
| n | bind something new on the selected input and layer |
| d | delete the selected use |
| / | search keys, buttons, chords, actions and sources. Enter jumps to the result on its drawing |
| u / U (Ctrl+Y) | undo / redo (`undo_depth` edits) |
| z | throw the draft away |
| R | start from the vendor and host defaults |
| v | validate now (every edit also validates in the background) |
| w | apply the draft |
| e / i | export / import the binding map |
| f | cycle the controller drawing's family |
| D | rescan devices |
| m, right click | the action menu: every command is in it |
| ? | help |
| q, Esc | quit (asks when the draft is unsaved). Ctrl+C quits without writing |

Conflicts show before validation finishes. A use that collides with another
on the same layer and context is flagged `!` and drawn red. `mios-controls
conflicts` lists them.

## Navigation: keyboard, mouse and controller

- **Keyboard:** the arrows move between inputs. A move goes to the input
  straight ahead before a diagonal one, across staggered rows too. Tab
  switches between the drawing and the list of uses.
- **Mouse:** click keys, buttons, tabs, layers and list rows. A second click
  opens the item, and the wheel scrolls.
- **Controller:** d-pad or left stick = arrows, right stick = page up/down,
  South = Enter, East = back, West = the action menu, North = capture,
  Select = Tab, Start = help, the shoulders = previous/next layer, the
  triggers = previous/next view (`[desktop.controls.pad_nav]`). A held
  direction repeats after `nav_repeat_delay_ms`, then every `nav_repeat_ms`.
  The editor reads a pad only while its terminal has focus. The controller
  drawing follows the pad you used last.

The editor reads controllers that no other program grabs, private
`mios-input-fixture-*` pads, and mios-inputd's virtual pad (read only).
`[desktop.controls].pad_names` limits this to controllers whose name contains
one of the listed parts. Pads are grabbed only while a capture runs, so the
pressed button reaches nothing else. Mice are read during a capture and
never grabbed.

## Validation and apply

A draft is checked by the same collision analysis `mios-gate
keybind-collisions` runs (compiled from the same source). It is checked
against the compositor session `mios-gen render-hyprland --runtime` renders
for the draft (stdout only, nothing is written), and against the tmux-os keys
the generator projects. The controller layers are checked too: real button
names, no action on the Guide button, and actions that are implemented or
planned. Findings already present before your edit are shown but never block
it. Findings your draft adds block apply.

`w` writes only the user-tier paths listed in `[desktop.controls].editable`:
the global `bindings`, `controller.system_layer` and `controller.room`,
`[keybindings.tmux].bindings`, and the `bindings` arrays of
`[desktop.hyprland]`, `[desktop.screenshot]` and `[desktop.shaderglass]`. It
refuses in four cases: the file changed since it was read, anything else in
the user tier would change, a user tier tries to widen `editable`, or the
result would be empty. The write takes a lock, is atomic and mode 0600, and
keeps the previous tier as `mios.toml.controls-backup`
(`[desktop.controls].backup_suffix`). The compositor picks the change up on
its next configuration reload. mios-controls never reloads the session.

Tiers merge tables per key and replace arrays, so an edited array is written
whole and restoring defaults removes it. A Guide-layer button set below your
tier can be moved (the two buttons swap) but not emptied.

## Input latency

Terminal input and controllers each have their own OS thread. The terminal
thread blocks only on input, and the pad thread blocks in `poll(2)`. Both are
pinned to one CPU (`[desktop.controls].input_cpu`, default the last CPU the
process may use). Drawing, validation and file IO run on the remaining CPUs,
so input never waits behind a redraw or a validation run. The pad thread
wakes on a timer only while a direction is held.

## Tunables (`[desktop.controls]`)

| Key | Meaning |
|---|---|
| `input_cpu` | CPU of the input threads (-1 = automatic) |
| `glyphs` | `auto`, `unicode` or `ascii` |
| `capture_timeout_ms` | how long a capture waits |
| `stick_threshold`, `trigger_threshold` | deflection that counts as a direction / a press |
| `nav_repeat_delay_ms`, `nav_repeat_ms` | held-direction repeat |
| `undo_depth` | edits kept for undo |
| `pad_names` | controllers the editor reads (empty = all) |
| `editable` | user-tier paths apply may write |
| `pad_nav` | controller button -> editor verb |
| `pad_families` | controller families: vendor ids, name hints, layout, labels, VT labels |

## Headless use

```
mios-controls list [--json]
mios-controls check
mios-controls uses INPUT [--json]            # key:return, mouse:275, pad:BTN_SOUTH or a chord
mios-controls search TEXT [--json]
mios-controls conflicts [--json]             # exit 1 when there are any
mios-controls layout keyboard|mouse|controller|bindings|devices [--width W] [--height H]
                [--family F] [--layer L] [--glyphs auto|unicode|ascii] [--select INPUT]
mios-controls devices [--json]
mios-controls profile DEVICE [--json]        # one device (index or name part) and what is bound on it
mios-controls set ID [--chord C] [--action A] [--contexts a,b] [--passthrough a,b] [--bypass C] [--tier N] [--apply]
mios-controls add ID --chord C --action A [...] [--apply]
mios-controls remove ID [--apply]
mios-controls pad BUTTON ACTION [--apply]
mios-controls room BUTTON ACTION [--apply]
mios-controls rebind REF --to INPUT [--layer L] [--apply]
                # REF = global:ID | bypass:ID | guide:BTN | room:BTN | tmux:KEY | table:PATH#N
mios-controls restore-defaults [--apply]
mios-controls export FILE
mios-controls import FILE [--apply]
```

`layout` prints the same drawing the TUI shows, as text. Editing verbs print
the user-tier binding paths they would write (never the rest of the file,
which also holds credentials) and the validation result. Exit codes: 0 valid,
1 the draft adds findings, 2 error, 64 usage. Exports contain the binding map
alone, and imports refuse anything else.
