<!-- AI-hint: Operator guide to mios-controls, the terminal binding editor over the layered [keybindings.global] map: diagrams, contexts, collisions, the draft -> validate -> apply workflow and its headless verbs. -->
<!-- AI-related: tools/native/mios-controls/src/main.rs, usr/share/mios/mios.toml [keybindings.global], usr/share/doc/mios/keybinds.md, tests/test-mios-controls.py -->

# MiOS Controls

`mios-controls` edits the one MiOS binding map, `[keybindings.global]` in the
layered `mios.toml`, from a terminal. It runs anywhere a terminal does: a
desktop terminal, a tmux-os pane, a Linux VT, SSH or ttyd. There is no second
binding store: every change is a draft of your user tier
(`~/.config/mios/mios.toml`), validated before it can be written.

## Screen

| Pane | Shows |
|---|---|
| Peripherals | the keyboard map, the controller map, and every connected keyboard and controller (read only; nothing is opened or grabbed) |
| Keyboard / Controller | a diagram: bound keys or Guide-layer buttons highlighted, the selected binding's chord in bold, buttons covered by the input-ownership contract underlined |
| Selected | chord, bypass, action, contexts, passthrough, where it is active, collisions, and for a button its system-layer action and fixed roles (Guide, cursor mode, mode override, Hypr3D toggle) |
| Bindings | every binding with `!` for a finding and `*` for a draft change; the `here` column follows the context picked with `[` and `]` |

`here` reads `on` (the host handles it), `->app` (passed through to the
focused app or VM), `app` (an application-owned chord) or `-` (not active in
that context).

## Keys

| Key | Action |
|---|---|
| Tab / Shift+Tab | next / previous pane |
| arrows, PgUp/PgDn, Home/End | move |
| Enter | edit the chord (table), set the Guide action (controller), jump to the key's binding (keyboard) |
| c a x p b t | chord, action, contexts, passthrough, bypass, tier of the selected binding |
| n / d | new binding / delete (table) or remove a user-tier button (controller) |
| [ ] | context shown in the `here` column |
| v | validate now (every edit also validates in the background) |
| w | apply the draft |
| R | put the vendor and host defaults in the draft |
| e / i | export / import the binding map |
| u | revert the draft |
| ? | help |
| q, Esc | quit (asks when the draft is unsaved); Ctrl+C quits without writing |

The mouse selects panes and rows and the wheel scrolls where the terminal
reports it. Every action has a key, so a controller that projects arrows,
Enter, Escape and Tab drives the whole editor.

## Validation and apply

A draft is checked by the same collision analysis `mios-gate
keybind-collisions` runs (compiled from the same source). It is checked
against the compositor session `mios-gen render-hyprland --runtime` renders
for the draft (stdout only, nothing is written), plus checks of the
controller system layer: real button names, no action on the Guide button,
and actions that are implemented or planned. Findings already present before
your edit are shown but never block it. Findings your draft adds block apply.

`w` writes only `[keybindings.global]` `bindings` and
`controller.system_layer`. It refuses when the file changed since it was read,
when anything outside `[keybindings]` would change, or when the result would
be empty. The write is atomic and mode 0600, and the previous tier is kept as
`mios.toml.controls-backup` (`[keybindings.global.controls].backup_suffix`).
The compositor picks the change up on its next configuration reload;
mios-controls never reloads the session.

Tiers merge tables per key and replace arrays. So an edited bindings list is
written whole, and restoring defaults removes it. A system-layer button set
below your tier can be rebound but not removed.

## Input latency

Terminal input has its own OS thread, which blocks only on input. It is
pinned to one CPU (`[keybindings.global.controls].input_cpu`, default the
last CPU the process may use), and drawing, validation and file IO run on the
remaining CPUs. Input is therefore never queued behind a redraw or a
validation run. Reserving that CPU from other processes needs a host CPU
isolation setting and is not done by the app.

## Headless use

```
mios-controls list [--json]
mios-controls check
mios-controls devices [--json]
mios-controls set ID [--chord C] [--action A] [--contexts a,b] [--passthrough a,b] [--bypass C] [--tier N] [--apply]
mios-controls add ID --chord C --action A [...] [--apply]
mios-controls remove ID [--apply]
mios-controls pad BUTTON ACTION [--apply]
mios-controls restore-defaults [--apply]
mios-controls export FILE
mios-controls import FILE [--apply]
```

Editing verbs print the user-tier `[keybindings]` they would write (never the
rest of the file, which also holds credentials) and the validation result.
Exit codes: 0 valid, 1 the draft adds findings, 2 error, 64 usage.
Exports contain the binding map alone, and imports refuse anything else.
