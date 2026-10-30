<!-- AI-hint: Two-sided tests (positive and negative control) for every step of the global bindings spec. -->
<!-- AI-related: docs/design/mios-global-bindings-final.md, tests/test-keybind-collisions.py -->
# MiOS global bindings: two-sided tests

Every change has a **positive control**, which shows that the change does what it should, and a **negative control**, which shows that it does not break or leak anything nearby. A test that cannot fail is not a test, so each negative control names the exact wrong output it rejects.

How the tests are run:
- **gate**: `/usr/bin/mios-gate keybind-collisions --root <R>`. Use a scratch root holding `usr/share/mios/mios.toml` and `usr/share/mios/hyprland/hyprland.lua` for planted cases.
- **check**: `MIOS_INPUTD_SSOT`/`--root` with `mios-inputd check`.
- **replay**: `mios-inputd --root <R> replay < case.json`. It is pure: `"executed":false`, and no device or socket is opened.
- **unit**: `cargo test` in the owning crate.
- **live**: operator only, on a disposable session. Never run hyprctl dispatch or eval hooks from an agent (MEMORY: Hyprland live-testing safety).

Replay event codes:
- EV_KEY = 1. SYN = kind 0, code 0.
- Buttons: South 304, East 305, North 307, West 308, LB 310, RB 311, LT 312, RT 313, Select 314, Start 315, Guide 316, L3 317, R3 318.
- Shorthand: `K(c,v)` = `{"kind":1,"code":c,"value":v}`.

Baseline that every step must keep: the gate reports 0 findings on `/` (today: 17 declared, 23 compositor-table and 40 literal lines). `mios-controls check` reports 0 findings.

## S0: SSOT-only

| ID | Positive control | Negative control |
|---|---|---|
| S0-merge | Splice the patch (FINAL.md, "How to merge"). gate → 0 findings, and `mios-inputd check` → ok. `python3 -c tomllib.load` parses the file. Already simulated: S0 and all-steps states both give 0 findings. | Plant `bypass = "SUPER+F10"` on the fullscreen row. gate and check must fail ("bypass must use bypass_mod"). Truncate the file to 0 bytes. The atomic-write step must refuse to rename it (size check). |
| C-06 | After regen, hyprland.lua contains `escapes["SUPER+f11"]="SUPER+CTRL+f11"`. Live: a fullscreen game, then Super+Ctrl+F11, leaves fullscreen. | In the same game, plain Super+F11 must do nothing (still inhibited, hyprland.lua:319). In desktop-2d, Super+F11 toggles exactly once (no double handle). |
| C-29 | `escapes["SUPER+escape"]="SUPER+CTRL+escape"` is emitted. Live: the overlay opens over a captured app with Super+Ctrl+Escape. | Plain Super+Escape inside a VM window reaches the guest (`passthrough = ["vm-passthrough"]`), and the host overlay does not open. |
| C-24 | gate: plant a tier-1 row `chord = "CTRL+b"`, contexts all. It must fail rule 3 ("shadows reserved terminal chord"). | The vendor map with the change → 0 findings. A row with `scope = "app"` on `CTRL+b` stays clean. |
| C-15a | replay `desktop-2d`: `K(316,1) K(0x222,1) K(0x222,0) K(316,0)` → actions `["hypr:movefocus left"]`. | Same events in `game` → no action; the app receives the d-pad key. On a HAT pad (ABS_HAT0X) under Guide → no action until I4, and no HAT event is forwarded to the app (router.rs:589-594). |
| C-26a | `grep -c schema.md usr/share/mios/mios.toml` = 0, and the tier-4 comment precedes `[keybindings.global.controller]`. | The gate and the drift checks (`mios-gate` full run) stay green after regen. A stale globals.sh must fail projection drift until it is regenerated. |

## I1: inputd P0 (live bugs)

| ID | Positive control | Negative control |
|---|---|---|
| C-01 | replay `app-capture` (operator decision 2026-10-29: no class captures the controller, so a `game` window is uncaptured until North): `K(316,1) K(314,1) K(314,0) K(316,0) K(311,1) K(311,0)` → actions are exactly `["input:cursor on"]`, and `app` contains `K(311,1)`. **The pre-I1 binary emits `"mios:shaderglass toggle"` here, which reproduces the live bug.** Also: Guide+East after the escape → `app` gets 316 and 305, and there is no `hypr:killactive`. Start → `input:cursor off`. | The same sequence in `desktop-2d` must still emit `"mios:shaderglass toggle"` (uncaptured behavior kept). North after the escape must not emit `input:window on`. |
| C-02 | unit: `input:window off` Lua adds `release_tag` and removes `capture_tag`. With `capture_fullscreen_tag` present it sets fullscreen off. Live: North hold, then L3+R3 hold, leaves the window windowed, and Super+C works again. | A `looking-glass-client` window with `release_tag` must still classify as `vm-passthrough` (inputd and Lua), and host Super chords stay disabled there. A fullscreen that the user made with Super+F11 must not be undone by a release (there is no `capture_fullscreen_tag`). |
| C-03 | unit: after `capture_window`, a snapshot without the tag within `context_stale_ms` keeps the override pending. A snapshot with the tag commits it. | A failing `input:window` child (exit 1) reverts the override. A pending capture that is never confirmed reverts after `context_stale_ms`, and the app does not keep the pad. |
| C-04 | Property test: random event streams in every context. No frame has both a non-neutral pad.bin and a non-neutral vpad axis. | A mutant that forwards to the vpad in the room must fail the property. |
| C-05 | `enabled=false` → serve exits 0. No seat (SSH login) → serve idles with exit 0 and no restart. Bridge error → exit 1, restart, and after 5 starts in 60 s the unit stops (start limit). | `mios-gamepad-hypr3d` is never executed: `recovery_argv` is absent, and check rejects it if it is planted. |
| C-27 | The fixture uses `captured_system_layer=false` and passes. The L3+R3 hold in a captured window of any kind (a `game` class included) → `input:window off` once. Live: tests/test-inputd-ownership-live.py drives the same transcript for every window kind. | With the fixture flipped to `true`, the half-capture tests must fail, so the fixture choice matters. |

## G1 / U1: mios-gen and mios-unit-gen

| ID | Positive control | Negative control |
|---|---|---|
| C-36 | Generated session-scale handles carry `escape=true`. Live: Super+Ctrl+= scales while a fullscreen game is captured. | Super+C (not on bypass_mod) stays disabled in app-capture. A handle on `SUPER+ALT+…` does not become an escape. |
| C-35 | A Lua unit or replay of `MIOS_INPUT_POLICY.context()`: a window that is the room `input_owner` → `app-capture`. With `release_tag` → not app-capture. | Tag a vm-class window with `release_tag` → still `vm-passthrough`. GT parity test: the Lua predicate and the inputd template agree on all 2^4 combinations of {fullscreen, capture_tag, input_owner, release_tag}. |
| C-22 | Enable the `#@G1` screenshot row → hyprland.lua has exactly one `Print` handle and one `SUPER+CTRL+Print` escape. gate → 0. | Today's generator with the row enabled emits a second `mios_native_hl.bind("Print",…)`. The new gate check (literal duplicate) must report it. The session-scale rows still get their dispatch binds, because no literal bind exists for them. |
| C-21 | gate literal count rises by 20 (workspace binds), and the dynamic count falls by 2. | Plant `SUPER+1` as a tier-1 row without passthrough → rule 2 finding (verified in critique §0). |
| C-25 | Remap `keymap` to `SUPER+F1` in a scratch user tier → the overlay shows `SUPER+F1` and the nav/Guide tables. | The overlay never shows the stale `SUPER+Escape` after that remap. The "L3 + R3" label follows `toggle_chord` when it is changed to `["BTN_TL","BTN_TR"]`. |
| C-31 | hyprland.lua contains a device rule `mios-vctl-keyboard` → `kb_layout us`. Live with `kb_layout=fr`: controller L3 in Firefox opens a new tab (Ctrl+T). | A physical keyboard keeps the `fr` layout (type `a` and get `a`). |
| C-10' | The generated tmux conf has `command-alias[…] mios-menu=display-menu … -x C -y C`, with items identical to the MouseDown3 menu. | unit-gen still rejects a binding key `PPage` (lib.rs:268): a planted row must error. The existing menu bindings are unchanged. |
| C-18 | (PLAUSIBLE) With `mouse_terms`, `tmux show -s terminal-features` contains `linux:mouse`, and gpm clicks select panes on a VT. | Without it, nothing changes, and the controller VT path still works (it does not use the mouse). |

## I2: nav grammar

Landed. Router unit tests `nav_contract` (C-08 timing, INV-6, R-23, R-26, C-11, C-13, grammar), runtime unit tests
(C-09 discovery over a fake /proc, C-30 argv, C-32 text), tests/test-inputd-nav.py (real binary; with
MIOS_INPUTD_UINPUT_TEST=1 a grabbed fixture keyboard, and a fixture pad driving the bridge against a fake compositor
socket and a private tmux server), tests/test-inputd-nav-live.py (MIOS_LIVE_NESTED=1, an owned nested session).
Old-binary negative controls: MIOS_INPUTD_OLD_BIN = the I1 binary (uncaptured buttons reach the pad, cursor mode drops
South, no OSD). Not provable here: "an SSH client attached to the same session never sees its focus changed" (tmux's
active pane is per window, shared by every client of that window).

| ID | Positive control | Negative control |
|---|---|---|
| C-07 | `udevadm info` on the virtual keyboard shows `ID_INPUT_KEYBOARD=1` and no `LIBINPUT_IGNORE_DEVICE`. `key:ENTER` produces KEY_ENTER. | The virtual pad (`mios-vctl-event*`) still has `LIBINPUT_IGNORE_DEVICE=1`. The bridge never grabs a physical keyboard: discovery rejects devices without BTN_SOUTH (linux.rs:125). |
| C-08 | replay `desktop-2d`, South tap (press, release at 100 ms) → `key:ENTER`. Hold 600 ms → `key:SHIFT+ENTER` once. D-pad held for 1 s → 1 + (1 + ⌊(1000-400)/60⌋) = 12 arrows. | Hold South for 2 s → exactly one hold action (no repeat). Hold a D-pad direction, then focus moves to a `game` window → the arrow repeat stops, and every virtual key is released (INV-6). Two pads each pressing one stick → no L3+R3 chord. A user tier that sets `BTN_SOUTH = ["key:SPACE","none"]` → hold does nothing (atomic array replace). Setting `BTN_SOUTH = ""` → validation error. |
| C-09 | A focused Alacritty running tmux → profile `terminal_tmux`. A plain Alacritty → `terminal_plain`. | In `terminal_plain`, LT → `key:SHIFT+PAGEUP`, and no `tmux` process is spawned. A bare shell never receives KEY_LEFTCTRL+KEY_B. |
| C-30 | `tmux:key p` runs `tmux -L mios-human send-keys -c <tty> -K C-b p` (argv in the replay `actions` resolution). `tmux:focus left` at the left edge → `hypr:movefocus left`. | `tmux:key q` (not bound in the SSOT) → validation error. `tmux:focus left` when a pane exists to the left → `select-pane -L` only, with no hypr action. An SSH client attached to the same session never sees its focus changed by C-b h. |
| C-11 | Cursor mode: South → `key:ENTER`. Left stick X → REL_HWHEEL events. | In cursor mode, RT still sends BTN_LEFT, and no `key:CTRL+TAB` is sent. |
| C-13 | L3 tap → `key:CTRL+t` on release. L3 then R3 within `debounce_ms` → room toggle only. | Slow overlap (L3 down, R3 down 400 ms later) → no singles. L3 pressed while the left stick is deflected → nothing. Guide+L3+R3 → no lock + latch double fire. |
| C-32 | Capture and release each send one notification that names the window class. | No notification on a focus change without a capture change. |

## I3: console

| ID | Positive control | Negative control |
|---|---|---|
| C-16 | With the compositor stopped and the user on tty3 → context `console`, and the D-pad sends arrows into the miosd selector (sessions.rs:477-484). After a compositor restart → the new socket is found without a service restart. | With the greeter or another user active on seat0 → the grab is released, and no key is projected. On an SSH-only login → serve idles (no `/dev/input` access) and does not crash-loop. gate: a compositor literal bind is not active in `console` (R-09). A console row using `hypr:` → C-23 finding. |
| C-17 | VT·tmux and VT·plain: L3+R3 → `Ctrl+Alt+F<VTNr>` of this user's graphical session. | With no graphical session → no key is sent. On the desktop, L3+R3 still toggles the room. |
| T-I3-4 | `showkey -a` on a VT: Shift+Tab prints `^[[Z` → enable O-6. | If it prints `^I`, keep VT West hold = none. |
| console_plain | Every non-none row produces only Enter, Esc, Ctrl+C, Tab or arrows. | `LS_UP` on a bare VT shell → no output (it must not be PageUp, which /etc/inputrc:21 binds to history search). |

## I4 / P1 / M1 / GT

| ID | Positive control | Negative control |
|---|---|---|
| C-15b | DualSense (HAT): Guide + d-pad left → `hypr:movefocus left`. Guide + left stick up → `mios:volume up` once per flick. Guide + right stick right → `hypr:movewindow right`. | Without Guide, the HAT in `game` reaches the app unchanged. Under Guide, no pointer motion or clicks are sent. |
| C-19 | `mios-inputd resolve-action mios:system-monitor` → the alacritty argv. | `quickshell:monitor-toggle` → "no installed handler" (the old alias is gone from config.rs:136). |
| C-14a/b | Room: East → `hl.plugin.hypr3d.cancel()` drops an active grab. L3 → sprint toggles. R3 → recenter. Select hold → keymap. | Room Select tap still cycles the view in ≤ 1 frame after release. Outside the room, East is `key:ESC`, not `cancel`. `hypr3d:unknown` → whitelist error (runtime.rs:155). |
| C-28 | xpad pad: `mios-inputd observe` shows physical Y (top) as 0x133 → no fixup is needed. If it shows 0x134, add the fixup and re-run: top → BTN_NORTH. | The DualSense must never get a fixup (its top button already reports 0x133). |
| C-33 | Session selector: Tab → new session. | Esc still drops to a shell. `j`/`k` still move. |
| C-34 | `mios-controls list --json` includes `nav`, `room` and `toggle_chord`. `check` on a user tier with `nav.desktop.BTN_SOUTH = "bogus"` → finding. | `mios-controls --root <scratch>` must read `<scratch>` (today it does not, which was observed in this review). The TUI conflict note shows the toggle chord after `hypr3d_toggle_chord` is dropped. |
| C-23 | Remove `BTN_EAST` from `nav.desktop` → gate finding "desktop-2d: BTN_EAST unmapped". | The vendor map → 0 findings. `"none"` counts as mapped. `"app"` in a nav profile → finding. |
| C-16g | gate CONTEXTS include `console`. A tier-1 row without contexts is not active in `console`. | A row with `contexts = ["console"]` and a `hypr:` action → finding. |
| C-26b/c | `doc_refs` gate: keybinds.md exists and the SSOT and gate header cite it. Every `[step X]` marker refers to a step listed in FINAL.md §7. | grep the shipped keybinds.md for "Outside VM/game contexts" and "keeps both buttons" → 0 hits. |
