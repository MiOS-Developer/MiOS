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
| C-30 | `tmux:key p` runs `tmux -S <client socket> send-keys -c <tty> -K C-b p` (argv in the replay `resolved` list). `tmux:focus left` at the left edge → `hypr:movefocus left`. | `tmux:key r` (not bound in the SSOT; `q` is bound since U1) → validation error. `tmux:focus left` when a pane exists to the left → `select-pane -L` only, with no hypr action. An SSH client attached to the same session never sees its focus changed by C-b h. |
| C-11 | Cursor mode: South → `key:ENTER`. Left stick X → REL_HWHEEL events. | In cursor mode, RT still sends BTN_LEFT, and no `key:CTRL+TAB` is sent. |
| C-13 | L3 tap → `key:CTRL+t` on release. L3 then R3 within `debounce_ms` → room toggle only. | Slow overlap (L3 down, R3 down 400 ms later) → no singles. L3 pressed while the left stick is deflected → nothing. Guide+L3+R3 → no lock + latch double fire. |
| C-32 | Capture and release each send one notification that names the window class. | No notification on a focus change without a capture change. |

## I3: console (landed)

Automated in tests/test-inputd-console.py (real binary; `MIOS_INPUTD_UINPUT_TEST=1` for the fixture pad, pre-I3 pair in `MIOS_INPUTD_OLD_BIN` + `MIOS_INPUTD_OLD_SSOT`) and tests/test-inputd-console-live.py (`MIOS_LIVE_NESTED=1`). A pty stands in for the VT, logind is a private copy of its state files, gpm is a private socket, tmux runs with a private `TMUX_TMPDIR`.

| ID | Positive control | Negative control |
|---|---|---|
| C-16 | This user's tty session active (private logind state) → context `console`: South = `\r`, West hold = ESC TAB, RT = ESC[6~, LS up = ESC[A in the VT pty. A VT tmux client behind the console bridge is found → `console_tmux`: LB/RB switch the client's session, North zooms. A killed compositor and a new one in the same runtime dir → `--rediscover` follows the new socket without a restart. | The greeter active → `seat: released`, another reader can EVIOCGRAB the pad, South writes nothing; this user active again → owned, South = `\r`. Without rediscovery (the pre-I3 binary) the new compositor never gets a key. The right stick at the left pane edge on a VT → `edge`, no compositor focus. SSH-only login → serve idles (unit test). A compositor literal bind is not active in `console` (R-09, unit test). A console row using `hypr:` or `quickshell:` → validation error (C-23). The pre-I3 binary rejects the I3 SSOT and the `console` replay context. |
| C-17 | VT·tmux and VT·plain: L3+R3 → `vt_switch: CTRL+ALT+F2` (this user's graphical VTNr 2), no bytes on the VT, no room toggle. | No graphical session → `vt_switch: null`, nothing sent. On the desktop, L3+R3 still toggles the room. |
| T-I3-4 / O-6 | Decided from the installed keymaps (no VT may be used): Shift+Tab = `Meta_Tab` in both `us` keymaps, and linux `kcbt=\E^I` → West hold = `key:SHIFT+TAB` (ESC TAB). | Ctrl+Tab stays plain Tab. |
| console_plain | Operator decision C-18: every non-none row produces only Enter, Esc, Ctrl+C, Tab, BTab, arrows, PageUp/PageDown, cursor mode or the VT switch. | `LS_UP` on a bare VT shell → Up (ESC[A), never PageUp (history search, /etc/inputrc:21). North, Guide, L3, Select → nothing. |
| C-18 bridge | tmux `mouse on` → the bridge connects to gpm (vc = its VT, every event, gpm keeps the pointer); a gpm click selects the left pane, a wheel-up enters copy mode in the pane under the pointer; an evdev pointer (FIFO of input_event) click selects too; reports are SGR 1006. The client's size and exit status pass through. | tmux `mouse off` → the gpm connection is closed and nothing is reported. `--mouse off` holds no source. The VT never receives `\e[?1000h/1002h/1003h/1006h` through the bridge, while a client attached directly does. The pre-I3 binary has no `console-bridge`. |
| R-21 | `map --text` lists every context and both halves; `tmux:keymap` → `display-popup -c <client> -E … /usr/bin/less -R <file>` (argv form). | The pre-I3 binary has no `map --text` and refuses `tmux:keymap`. |

## I4 / P1 / M1 / GT

| ID | Positive control | Negative control |
|---|---|---|
| C-15b (landed I4) | DualSense (HAT): Guide + d-pad left → `hypr:movefocus left` once (no 400/60 repeat). Guide + left stick up → `mios:volume up` once per flick; LS left/right → media previous/next. Guide + right stick right → `hypr:movewindow right` (`hl.dsp.window.move`). Analog-only LT (xpad) under Guide → `hypr:movetoworkspace e-1`. Replay (test-inputd-platform `Grammar`), the fixture HAT pad against a fake compositor (`Fixture.test_c15b_guide_layer_on_a_hat_pad`), router `guide_layer_contract`. | Without Guide the HAT is nav arrows, and in a capture it reaches the app unchanged (captured_system_layer=false: the app gets Guide + HAT). Under Guide, no pointer motion, wheel or click. A stick deflected before Guide does not fire. A DualSense BTN_TL2 + ABS_Z fires once. On a VT Guide stays no modifier. The pre-I4 pair swallows HAT and sticks under Guide (and its quick Guide+HAT opens the drawer). |
| C-19 (landed I4) | `mios-inputd resolve-action mios:system-monitor` → the alacritty argv (`--action system`). | `quickshell:monitor-toggle` → "no installed IPC handler" (the old alias is gone from config.rs). The pre-I4 pair still resolves the alias. |
| O-7 (landed I4) | Fixture `xpad` (045e:028e): the physical top button (reported 0x134) captures (North), the left one (0x133) is West (Tab); fixture `dualsense`: positional, no quirk, same result. `xpad-dpad-buttons`: BTN_TRIGGER_HAPPY1..4 navigate and Guide + one focuses. A captured app's clone receives the driver's own code (0x134). | The pre-I4 binary reads the xpad top button as West (Tab, no capture). A non-one-to-one remap, a code outside 0x100..0x2ff and a malformed id are refused. |
| Agent pad (landed I4) | `vpad create` → a uinput pad; `stick --ms 400` → a monotone sampled ramp (>= 40 values, no step above 12 % of the range, small first/last steps) reaching full deflection; `--hold-ms` eases back to rest by itself; button/d-pad taps; the trigger button follows the analog value; `destroy` and `idle_exit_s` remove the device; the [verbs.virtual_controller] template renders an argv that drives it. | A raw fixture write teleports in one sample (contrast). Bad ease, BTN_TL2 as a button, a bad d-pad dir, a second holder and a bad pad key are refused. The pre-I4 binary has no `vpad`. |
| Nested passthrough (landed I4) | `bridge --nested <seat>` / `vpad assign`: follows the session's compositor and private room packet (an eased ramp reaches the room as >= 8 intermediate values), projects key: into the session compositor, releases its grab while an app in the session owns the pad and grabs again on release, exits when the session ends. The host bridge skips an assigned node, hands over a node assigned later, and never owns a seat pad. Live (test-inputd-vpad-live): the vpad walks and turns a nested PiP session's Hypr3D camera with a gradual speed profile. | A raw step reaches the room packet in one sample and reaches walking speed in about half the eased rise time (contrast). An unassigned pad moves nothing in the session. `assign` refuses a session that does not hold the pad, and an unknown seat. The pre-I4 bridge grabs a node assigned to a session and has no `--nested`. |
| C-14a/b | Room: East → `hl.plugin.hypr3d.cancel()` drops an active grab. L3 → sprint toggles. R3 → recenter. Select hold → keymap. | Room Select tap still cycles the view in ≤ 1 frame after release. Outside the room, East is `key:ESC`, not `cancel`. `hypr3d:unknown` → whitelist error (runtime.rs:155). |
| C-28 / T-C28 | xpad pad, with the vendor quirk disabled in a user tier (`[keybindings.global.controller.quirks.xpad] enabled = false`): `mios-inputd observe` shows physical Y (top) as 0x134 → the vendor quirk is right; with the quirk on, top → BTN_NORTH (capture). If observe shows 0x133, drop the face-order pair from the quirk. Not run yet (no physical Xbox pad here); I4 proved the quirk with uinput fixtures only. | The DualSense must never get a fixup (its top button already reports 0x133); the vendor quirk matches no hid-playstation device. |
| C-33 | Session selector: Tab → new session. | Esc still drops to a shell. `j`/`k` still move. |
| C-34 | `mios-controls list --json` includes `nav`, `room` and `toggle_chord`. `check` on a user tier with `nav.desktop.BTN_SOUTH = "bogus"` → finding. | `mios-controls --root <scratch>` must read `<scratch>` (today it does not, which was observed in this review). The TUI conflict note shows the toggle chord after `hypr3d_toggle_chord` is dropped. |
| C-23 | Remove `BTN_EAST` from `nav.desktop` → gate finding "desktop-2d: BTN_EAST unmapped". | The vendor map → 0 findings. `"none"` counts as mapped. `"app"` in a nav profile → finding. |
| C-16g | gate CONTEXTS include `console`. A tier-1 row without contexts is not active in `console`. | A row with `contexts = ["console"]` and a `hypr:` action → finding. |
| C-26b/c | `doc_refs` gate: keybinds.md exists and the SSOT and gate header cite it. Every `[step X]` marker refers to a step listed in FINAL.md §7. | grep the shipped keybinds.md for "Outside VM/game contexts" and "keeps both buttons" → 0 hits. |
