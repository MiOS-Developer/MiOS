<!-- AI-hint: Final global controller + keyboard binding spec: 20 controller inputs x 10 contexts (tap/hold), keyboard map, ownership-contract integration and the staged implementation plan by owner (S0, I1, I2, I3, P1 and I4 landed; G1 U1 GT M1 pending). -->
<!-- AI-related: usr/share/mios/mios.toml [keybindings.*], docs/design/mios-global-bindings-staged.patch.toml, docs/design/mios-global-bindings-tests.md, usr/share/doc/mios/keybinds.md, tools/native/mios-inputd -->
# MiOS global bindings: FINAL spec (controller + keyboard)

Status: FINAL SPEC. It merges design.md with every critique.md item, and records each item's disposition. Nothing in the repo or on the live system was changed.
Tree: HEAD ebece0e20 on `mios-desktop-integration`. Paths are repo-relative, and `/` is the deployed overlay of the same tree. Line numbers were taken from that HEAD.

Labels:
- **DECLARED**: present in the SSOT or in an SSOT comment.
- **IMPLEMENTED**: a code path acts on the input.
- **DOCUMENTED**: present in usr/share/doc/mios/keybinds.md.
- Status of a behavior: **[K]** kept, **[F]** fix, **[N]** new.
- Step tags (§7): S0, G1, U1, I1, I2, I3, I4, P1, GT, M1. A behavior tagged with a step is DECLARED in the patch but commented out, and becomes IMPLEMENTED when that step lands.

**I1 as landed (operator decision 2026-10-29, supersedes O-1/O-4 defaults for the controller):** capture and release are identical for every window kind. No class list (`game`, `vm-passthrough`) captures the controller; those classes only scope keyboard passthrough. inputd's controller owner is `app` only for `app-capture` (fullscreen==2, `capture_tag` or the room `input_owner`, each cancelled by `release_tag`). The legacy daemon unit has no `[Install]`.

**I2 as landed** (tools/native/mios-inputd/src/nav.rs, router.rs, runtime.rs; tests: router `nav_contract`, tests/test-inputd-nav.py, tests/test-inputd-nav-live.py). Differences from the text below:
- Every uncaptured window navigates alike (operator decision): `nav.contexts` maps `game` and `vm-passthrough` to `desktop` too, and `cursor_mode_contexts` lists every uncaptured context. A class never decides who gets the controller.
- `tmux:` verbs address the server of the client that discovery found (`tmux -S <socket>`, with the socket resolved from the client's `-L`/`-S` and `TMUX_TMPDIR`, because MiOS terminals set `TMUX_TMPDIR`), not `-L socket_name`. Which servers count is the SSOT list `nav.tmux_sockets` (`mios-human`, `mios-nested`). Discovery reads `/proc` only (once per second per focused terminal, no spawn).
- The HAT d-pad folds into `BTN_DPAD_*` for nav (the system-layer fold stays I4). Pads that report triggers only as axes (xpad: no BTN_TL2/TR2) get digital nav edges at `trigger_threshold`.
- `capture_osd_argv` (default `notify-send`) is the notifier; the OSD runs after the compositor confirmed the transaction, whose success marker now carries the window class.
- `inherits` is resolved in mios-inputd (nav.rs), not in mios-resolver. `tmux:menu` sends `<prefix> <[keybindings.tmux].menu_key>` to the client (U1's binding opens the generated MiOS menu there, with its own small-terminal and VT handling), instead of invoking the alias without a target client. `tmux:keymap` is the I3 popup (see I3 as landed).
- An empty `""` in any tier reaches the grammar and is a validation error (the Rust resolver does not drop it).
- The fixture tier with `--live-context` projects `key:` (send_key_state to the focused window), `hypr:` and `tmux:` into the followed (nested) compositor, so the live test needs no uinput keyboard on the operator's seat.

**I3 as landed** (tools/native/mios-inputd/src/console.rs, runtime.rs, router.rs, nav.rs; tests: router `console_contract`, runtime seat/rediscovery/VT-discovery tests, tests/test-inputd-console.py, tests/test-inputd-console-live.py). Operator decisions (2026-10-29) applied on top of the text below:
- **Seat view (C-16, INV-8).** inputd reads logind's runtime state files (users/, sessions/, seats/; no spawn per check). This user's active tty session selects `console` (its VTNr and TTY); this user's active graphical session follows the compositor; the greeter, a lock screen, another user or nothing active suspends: the grab is released, every output is neutralised and nothing is routed until this user is active again. serve starts for any seated login (a VT included) and idles for an SSH-only login. The unit is no longer part of the desktop (`PartOf=` dropped, `WantedBy=default.target`, from `[user_units]` via mios-unit-gen).
- **Rediscovery (C-16).** When IPC to the followed compositor fails, inputd re-selects the newest live top-level instance in `$XDG_RUNTIME_DIR/hypr` (lock pid alive, socket present, not nested inside another listed instance), at most every 250 ms, so a compositor restart needs no unit restart. If no compositor lives for `context_stale_ms` and this user has a graphical VT, the console context is used on that VT.
- **Console profiles.** `console_tmux` (a MiOS tmux client attached to the active VT, found through `/proc`, also below the console bridge) keeps the spec's tmux verbs. `console_plain` follows operator decision C-18: sticks and d-pad = arrows (repeat 500/250 on the stick), South = Enter, East = Esc (hold Ctrl+C), West and the bumpers = Tab / BTab, triggers = PageUp / PageDown, Start = cursor mode. Console rows may use only `key:`, `tmux:`, `mios:graphical-vt`, `pointer:cursor` and `none` (C-23, enforced by inputd's validator); `console = "vpad"` is rejected.
- **O-6 resolved.** West hold = `key:SHIFT+TAB` on the VT, the same stroke as on the desktop. T-I3-4 was decided from the installed keymaps instead of `showkey -a` (no VT may be used here): both `us` console keymaps (kbd `xkb/us.map` and `legacy/i386/qwerty/us.map`) map Shift+Tab to `Meta_Tab`, which the console sends as ESC TAB, and the linux terminfo has `kcbt=\E^I`.
- **Cursor mode on a VT.** `pointer:cursor` (a button value) toggles cursor mode from a nav profile (`console_plain` Start tap, `console_tmux` Start hold). The sticks then drive `mios-vctl-pointer`, which gpm draws on the VT; buttons keep their nav meaning.
- **C-17.** `mios:graphical-vt` strokes Ctrl+Alt+F<VTNr> of this user's graphical session on the uinput keyboard (the kernel keymap's `Console_<n>`). Without a graphical session, or beyond F12, nothing is sent. On the desktop the toggle chord still toggles the room.
- **R-08.** Guide is no modifier on a VT; a Guide tap fires the console profile's `BTN_MODE`. Guide holds, the room table, ShaderGlass, North capture and the legacy host actions never run in `console`. R-09: a host binding without `contexts` is not active in `console`.
- **`tmux:keymap` (R-21).** `display-popup -c <client> -E` with `[keybindings.global.controller.console].keymap_argv` (default `less -R {file}`, argv form, no shell) paging `mios-inputd map --text`, rendered to `$XDG_RUNTIME_DIR/mios-inputd-keymap.txt` (0600, atomic).
- **C-18 gpm -> tmux-os bridge.** `mios-inputd console-bridge -- <tmux client argv>` runs the client on its own pty and copies the VT to it. It consumes every mouse-mode DECSET/DECRST the client sends (9, 1000-1003, 1005, 1006, 1015), so the VT itself never enters mouse reporting, and while the client tracks the mouse it opens one pointer source: gpm (`gpm_socket`, routed by its VT, gpm keeps drawing its pointer) or an evdev pointer read without a grab (`pointer_names`, default the controller's `mios-vctl-pointer`, `cell_px` pixels per cell). Pointer events become SGR 1006 reports (or the legacy encoding when the client did not ask for 1006). With mouse mode off it holds no source, so gpm selection works on that VT. mios-terminal wraps a local VT client (fd 0 is `/dev/tty<N>`) when `controller.console.bridge = true`. tmux on TERM=linux already emits the mouse modes (the linux terminfo has `kmous`), so U1's `terminal-features ',linux:mouse'` is not needed.
- **Fixture tier.** `bridge --fixture-only --logind-state <dir> --console-tty <tty> --console-sink-fd <fd>` reads a private logind copy and writes console strokes as the bytes the Linux `us` keymap would deliver into a pty that stands in for the VT; `--rediscover` follows a private `XDG_RUNTIME_DIR`. No VT, no physical device and no real gpm is used by the tests.

**I4 as landed** (tools/native/mios-inputd/src/router.rs, vpad.rs, nested.rs, runtime.rs, linux.rs, config.rs; mios-resolver keybindings.rs; tests: router `guide_layer_contract`, config quirk/pseudo-key/agent-pad tests, vpad and nested unit tests, tests/test-inputd-platform.py, tests/test-inputd-vpad-live.py). Operator decisions (2026-10-29) applied on top of the text below:
- **C-15b Guide layer on every pad shape.** Under Guide (uncaptured, a compositor running, never on a VT) the HAT fires the system layer's `BTN_DPAD_*` once per press (no repeat), an analog-only trigger (xpad) fires `BTN_TL2/TR2` at `trigger_threshold`, and `LS_*`/`RS_*` fire once per stick flick (`nav.flick_on`, re-armed below `nav.flick_off`; a stick already deflected when Guide goes down is not armed). `LS_*`/`RS_*` are resolver pseudo-keys past KEY_MAX (`button_code` 0x300..0x307, `is_stick_pseudo`), accepted only as system-layer keys (never a chord, the room table, Guide, cursor or quirk code). With Guide held the sticks never move the pointer, scroll or click. A layer action makes the Guide press a modifier, so a quick Guide+HAT no longer also opens the drawer (the I3 router did). Vendor rows: `LS_UP/DOWN` = `mios:volume up/down` (wpctl, capped at 100 %), `LS_LEFT/RIGHT` = `mios:media previous/next` (playerctl), `RS_*` = `hypr:movewindow <dir>` (`hl.dsp.window.move({direction})`).
- **C-19.** `quickshell:monitor-toggle` is gone: `mios:system-monitor` (the [keybindings.actions] `system` desktop command, same argv) is the Guide+Start row.
- **O-7 per-driver quirk table** `[keybindings.global.controller.quirks.<name>]`: `drivers` (sysfs driver name) or `ids` (`vvvv:pppp`) match; `remap = { reported = "positional" }` (button names or hex codes), one-to-one. It applies to routing only; the cloned virtual pad gets the driver's own codes back, so a captured app (SDL maps by the clone's USB id) sees the device as it is. Vendor quirk `xpad` (driver xpad and the Xbox 360 / One S / Series USB ids): the top face button (Y, reported as 0x134 BTN_WEST) becomes BTN_NORTH and X (0x133) becomes BTN_WEST, and xpad's `dpad_to_buttons` codes BTN_TRIGGER_HAPPY1..4 become BTN_DPAD_LEFT/RIGHT/UP/DOWN. DualSense (hid-playstation) is positional and has no quirk. Proven with uinput fixtures that emulate both code layouts (`fixture-pad --layout xpad | xpad-dpad-buttons | dualsense`); T-C28 on a physical Xbox pad is still to be run (see §8 O-7).
- **The agent virtual controller** `mios-inputd vpad <op>` ([keybindings.global.controller.agent_pad], MCP tool [verbs.virtual_controller] = the same argv): one holder process per pad owns a uinput gamepad (DualSense-like, BUS_VIRTUAL `agent_pad.usb_id`) and a 0600 control socket under `$XDG_RUNTIME_DIR/agent_pad.socket_dir`. Ops: `create` (`--seat <s>` names it [desktop.nested.devices].virtual_pad_name), `stick` (x/y, `--ms`, `--hold-ms` then eases back), `trigger`, `button` (tap/press/release), `dpad`, `release`, `wait`, `status`, `info`, `assign`, `unassign`, `destroy`, `list`. Every stick/trigger change is an eased ramp (linear, in, out, in-out cubic) sampled at `rate_hz`, starting from the current value (an interrupted ramp continues without a jump) and never shorter than `min_ms`; the trigger buttons follow the analog value with hysteresis. The holder exits on `destroy`, SIGTERM or `idle_exit_s` at rest, and the device goes with it.
- **Nested-session controls passthrough** (the device binding itself is mios-gen's: `--device` / a seat preset / `virtual-pad`, bound into the session's masked /dev/input and listed in the [desktop.nested].registry_dir registry). inputd's side of that contract: the host bridge never opens a node a live session holds (and hands one over, outputs at rest and grab released, when a session claims it later), and never owns a seat's pad (a name matching virtual_pad_name). `mios-inputd bridge --nested <seat>` (also started by `vpad assign`) follows exactly that session: its compositor (XDG_RUNTIME_DIR / signature / display from the registry), its private room packet (`pad_state`; a session sharing the parent /tmp is refused), and only its assigned controller nodes. key:, hypr: and tmux: project into the session's compositor (never the host's uinput keyboard or pointer); there is no virtual pad clone (it would appear on the host): while an app in the session owns the pad, the bridge releases its grab and the app reads the assigned node itself, and the held toggle_chord still releases it. The bridge exits when the session ends. Live: an eased vpad ramp walks and turns the Hypr3D camera of a nested PiP session with a gradual speed profile (5 %->80 % rise about 0.29 s for a 700 ms ramp, against about 0.12 s for a raw stick step).
- **Fixture isolation.** Test pads and fixture-named agent pads (`mios-input-fixture-*`) are opened only by fixture-tier bridges; the udev rule also clears their joystick/input classification so no udev consumer (libinput, SDL apps, a game launcher's desktop controller mapping) treats one as a controller.

Deliverables in this directory:
- `ssot-keybindings.patch.toml`: the complete replacement tables.
- `keybinds.md`: the replacement doc.
- `tests.md`: two-sided tests.

## 0. Baseline and verification (read-only, 2026-10-29)

| Check | Result |
|---|---|
| `/usr/bin/mios-gate keybind-collisions --root /` | 17 declared, 23 compositor-table and 40 literal hl.bind lines (4 dynamic, not audited): **0 findings**. design.md:17 (12/35) is stale. |
| `mios-controls check` (build tree) | 0 findings. It validates only the 11 system_layer rows (validate.rs:107-145). |
| `~/.config/mios/mios.toml` | `grep -c keybindings` = 0, so vendor defaults are the effective map. |
| Gate on a scratch copy of the SSOT with the patch spliced in | **S0 state** (active lines only): 17 declared, **0 findings**. **All-steps state** (every `#@` line enabled, every `@drop` line removed): 18 declared, **0 findings**. Both states parse as TOML (Python tomllib). |
| `mios-controls --root <scratch> check/list` | **Not evidence.** It printed the same 11 system_layer rows for every root, so `--root` does not redirect its SSOT. The S0 system_layer rows were instead checked against validate.rs:123-142 by reading the code. They pass: `button_code` knows BTN_DPAD_* (mios-resolver keybindings.rs:129-132), and `hypr` is in action_namespaces. |
| Connected pad (`/proc/bus/input/devices`, sysfs capabilities) | DualSense. event15 has keys 0x130,0x131,0x133,0x134,0x136-0x13e and ABS 0,1,2,3,4,5,16,17. The d-pad is a **HAT, not BTN_DPAD_***, and BTN_TL2/TR2 are digital plus analog. event17 is the touchpad (ID_INPUT_TOUCHPAD=1, libinput). event16 is the motion sensors (ID_INPUT_ACCELEROMETER=1). |

## 1. Critique disposition

### 1.1 Required items (R-01..R-27)

| Item | Verdict | How it is applied |
|---|---|---|
| R-01 Half-capture leaks Guide and host buttons | **Accepted, verified.** router.rs:440 sets `captured=false` when `cursor` is on. Guide combos then run system_layer (router.rs:464-495), and legacy_action runs because `self.cursor` (router.rs:553). | **C-01 rewritten:** `half = app_owned && cursor`. In half-capture only `mode_override_chord` and Start are host input. Guide and every other button go to the vpad through the deferred-Guide replay (router.rs:447-454), and the sticks and LT/RT go to the pointer. The Half-capture column of §3 states this. |
| R-02 release_tag must not demote VM/game classes | **Accepted.** hyprland.lua:317-319 keeps only escape handles in vm-passthrough. | C-02: `release_tag` cancels only fullscreen==2, `capture_tag` and `input_owner`. A release on a VM client changes only inputd's controller owner (SSOT comment on `release_tag`). |
| R-03 Room capture leaks the keyboard | **Accepted, verified.** inputd counts `input_owner` (runtime.rs:84). The Lua `context()` does not (hyprland.lua:302-312). | **C-35 (modified):** mios-gen emits one predicate `MIOS_INPUT_POLICY.captured(w)` in hyprland.lua, covering fullscreen, capture_tag, input_owner and release_tag. The inputd context template (runtime.rs:77-86) calls it when present and falls back to its own copy, so there is one source at runtime. A GT parity test compares the two. |
| R-04 Nav sink flush rule | **Accepted, verified.** router.rs:200-203 returns early for 2D-to-terminal moves, and `release_outputs` (router.rs:242-269) covers only vpad and pointer. | INV-6 in §6. Part of C-08. |
| R-05 `/usr/bin/false` mitigation loops | **Accepted, verified.** main.rs:84-92 execs `recovery_argv` whenever the bridge is disabled or fails. | The S0 patch keeps `recovery_argv`. C-05 is inputd-only (I1): exit 0 when disabled, idle when there is no seat or device access, exit non-zero on a bridge error, and add a start limit to the unit. config.rs:251 validation and the field are removed in the same step (`@drop:I1`). |
| R-06 C-21 double-binds | **Accepted, verified** (global_bindings.rs:53 hard-codes 7 ids). Also changed: R-06 proposed keying the bind on `realized_by` presence, which would break the 5 session-scale rows, because they rely on the generated bind for realization (hyprland.lua:239-253). | **C-22 (modified):** mios-gen emits the dispatch bind only when no compositor table or literal bind has the same canonical chord. |
| R-07 Console C-b sequences without tmux | **Accepted.** | Removed by design. Every tmux action is a `tmux:` verb that inputd runs only against a tmux client it found (§3.3). Without a client the `*_plain` profile applies. |
| R-08 Guide layer on the console | **Accepted.** | VT: Guide hold = none, Guide tap = tmux MiOS menu (`console_tmux`) or none (`console_plain`). |
| R-09 `console` in the gate needs a scoped default | **Accepted.** | GT: compositor-sourced Bounds and the default row contexts become "all except console". Mirror this in inputd config.rs:8-16. |
| R-10 Xbox positional check | **Accepted, re-scoped.** The connected pad is a DualSense with positional codes (event15 has 0x133 and 0x134). | **C-28** is a P0 prerequisite **for xpad pads only**. The operator runs `mios-inputd observe` on an xpad pad before I2 ships to Xbox users (O-7). |
| R-11 Nav value semantics | **Accepted, all four parts.** | (a) A value is a string (fires on press) or a 2-array `[tap, hold]`. Arrays replace atomically. (b) `"none"` is the only unbind word and `""` is rejected. `"app"` is not a nav value. (c) `inherits = "<profile>"`, resolved once in mios-resolver. (d) `nav.contexts` is a **table** (per-key merge), so O-1 is reverted with one line. Docs warn about array replacement. mios-controls audits the resolved user projection (C-34). |
| R-12 Namespaces and chord shape | **Accepted.** | `mios:graphical-vt` is an inputd verb. `TOGGLE_CHORD` refers to the existing `toggle_chord` array, so there is no second chord syntax. `key` and `pointer` are added to action_namespaces at I2. |
| R-13 One pseudo-key vocabulary | **Accepted.** | BTN_*, BTN_DPAD_*, BTN_TL2/TR2, LS_*/RS_*, TOGGLE_CHORD, used everywhere (nav, system_layer, room). |
| R-14 Derive the prefix, pin the layout | **Accepted, CLI route.** | tmux verbs run `tmux -L <socket_name>` with `send-keys -c <client> -K <prefix> <k>`. That is a key-table lookup (tmux 3.7c man page, send-keys `-K`), so it is layout-independent and takes its prefix from `tmux_prefix`. The `key:` projection is pinned to `us` (C-31). |
| R-15 VT Shift+Enter | **Accepted.** | VT South hold = none. VT West hold = BTab (`key:SHIFT+TAB` = Meta_Tab = ESC TAB; O-6, landed in I3). |
| R-16 Rename needs inputd | **Accepted, verified** (config.rs:136, 675). | C-19 moves to I4 together with config.rs:136. The S0 patch keeps `quickshell:monitor-toggle`. |
| R-17 Hyprland direction words | **Accepted.** | `left/right/up/down` everywhere (hyprland.lua:105-108 style). |
| R-18 Per-input repeat | **Accepted.** | `[nav.repeat]`: arrows 400/60. LT/RT and VT paging 500/250. Right-stick flicks and holds never repeat. |
| R-19 Right stick trapped in tmux | **Accepted.** | `tmux:focus <dir>` checks `#{pane_at_<dir>}` and falls through to `hypr:movefocus <dir>` at the edge on the desktop. On a VT it stops at the edge. Keyboard `C-b h/j/k/l` stays unchanged, so an SSH user never moves the local desktop focus. |
| R-20 LB/RB on the VT | **Accepted.** | VT LB/RB = previous/next tmux session (`tmux:session-prev/next`). VT switching happens only on TOGGLE_CHORD (C-17). |
| R-21 Select hold = bindings everywhere | **Accepted.** | VT Select hold = `tmux:keymap` (a display-popup of `mios-inputd map --text`). |
| R-22 Captured terminal looks dead | **Accepted.** | C-32: `capture_osd`. Every capture and release shows "Controller -> <app>. Hold L3+R3 to release." The contract is unchanged. |
| R-23 Sloppy stick clicks | **Accepted.** | Any overlap of L3 and R3 cancels both singles. An L3 or R3 click is ignored while its own stick is outside `stick_deadzone`. |
| R-24 Fewer timing knobs | **Accepted.** | No `nav.hold_ms`. `capture_hold_ms` is the hold threshold and `stick_deadzone` is the deadzone. Only `flick_on`/`flick_off` and the repeat table are new. |
| R-25 One controller namespace | **Accepted.** | Room additions go in `[keybindings.global.controller.room]` (P1). Existing hypr3d keys migrate later; LegacyConfig still requires them (config.rs:380-405). |
| R-26 Guide tap definition | **Accepted.** | Guide tap fires on release before `capture_hold_ms`, with no other input in between. A Guide press cancels pending nav timers. |
| R-27 Bypass chords disabled in capture | **Accepted, verified.** The session-scale handles have no `escape` (hyprland.lua:239-253), and hyprland.lua:319 enables only escapes. | **C-36:** mios-gen marks every handle whose chord starts with `bypass_mod` as an escape. Rule: **in app-capture and vm-passthrough, exactly the bypass_mod chords reach the host.** The S0 patch corrects the SSOT comment that claimed this already works (old mios.toml:4328). |

### 1.2 Recommended items

| Item | Verdict |
|---|---|
| O-1 auto-detection through /proc/<pid>/fd | **Accepted as option O-5, default off (PLAUSIBLE).** A browser that serves the Gamepad API would be classed as a game. |
| Screenshot bypass | **Accepted, G1** (`screenshot-region` row, `bypass = "SUPER+CTRL+Print"`). It is blocked on C-22, which avoids a double Print bind. |
| Hold-to-approve | **Accepted.** Holds fire once, never repeat, and the OSD shows progress. |
| Configurator | **Accepted, as "TOML-only".** keybinds.md says so. mios-controls `list` prints the nav and room tables (C-34). |

### 1.3 Design items rejected or changed by this pass (new evidence)

| Item | Change | Evidence |
|---|---|---|
| C-10 tmux `PPage/NPage/(/)` prefix binds | **Rejected.** Replaced by tmux CLI verbs (C-30). | mios-unit-gen lib.rs:268 rejects any tmux binding key other than `Tab` or one lowercase letter, the mobile-safe rule. C-10 would fail generation. `menu_key = Space` would fail the same way, so the MiOS menu becomes a command alias instead (U1). |
| C-20 remove dead keys now | **Deferred.** | `hypr3d_toggle_chord` is now read by mios-controls tui.rs:809-810 (added after the verified pass). LegacyConfig requires `poll_ms`, `debounce_ms` and `cursor_mode_button` (config.rs:380-405). The legacy daemon reads `left_stick_mode` and `device_filter` (mios-gamepad-hypr3d:169-174, 212-220). All are marked `@drop:GT` or `@drop:I1`. |
| C-21 "declare every shipped bind" | **Reduced.** | The gate already audits literal binds, Super+Q and Super+R included (verified-docs F-05). A host or user tier that declares any row replaces the whole bindings array (R-11d), so growing it from 17 to about 60 rows makes overrides more dangerous. Final C-21: emit the workspace loop literally (hyprland.lua:133-136, the only unaudited binds), and add a row only where a bypass or contexts are needed. |
| C-05 SSOT-first | **Reversed** (R-05). | main.rs:84-92 |
| C-12 `guide_tap_action` key | **Folded** into the profiles as `BTN_MODE`. | One place per context. |
| C-14 keys in `[desktop.hyprland.hypr3d.controller]` | **Moved** to `[keybindings.global.controller.room]` (R-25). | |
| New: SUPER+CTRL+Escape keymap bypass (C-29) | **Added, S0.** You can see the bindings over a captured app or a VM. | The id is in the global_bindings.rs:53 list, so no double bind. The dynamic escape projection (hyprland.lua:323-331) realizes it. The gate gives 0 findings. |
| New: DualSense touchpad and motion | **Documented** as rows. MiOS adds no binding. | linux.rs:125 accepts only devices with BTN_SOUTH and ABS_X/Y, so the touchpad node (event17) stays with libinput as a host touchpad. |

## 2. Grammar (the whole design)

1. **Ownership decides first.** If the focused window is captured, the app owns every controller input. The only exceptions are the `toggle_chord` hold (release) and the `mode_override_chord` (half-capture). If it is uncaptured, MiOS owns the controller. The room is MiOS-owned. A VT has no window, so MiOS projects keys to it.
2. **Same control, same meaning** wherever MiOS owns input:

| Control | Meaning | Tap | Hold (`capture_hold_ms` = 500) |
|---|---|---|---|
| South | confirm | Enter | Shift+Enter (agent accept) |
| East | back / cancel | Esc | stronger back (Ctrl+C in terminals, Alt+Left in GUIs) |
| West | next field | Tab | Shift+Tab |
| North | take ownership (contract) | capture | fullscreen + capture |
| D-pad | move inside the focused thing | arrows, repeating | - |
| Right stick | choose what has focus | one flick = focus in that direction | - |
| Left stick | move / scroll the content | wheel (analog) | - |
| LT / RT | previous / next at tab scale | previous/next tab, window or page | - |
| LB / RB | ShaderGlass cycle / toggle (room: CRT) | | |
| Select | view / menu | context menu or tree | bindings overlay |
| Start | cursor mode (the sticks and triggers become a mouse; buttons keep their meaning) | | |
| Guide | tap = app drawer / menu; hold = system layer modifier | | |
| L3 / R3 | new / reset view | | |
| L3+R3 | tap = switch view (room / VT); hold = release capture | | |

3. **Value grammar** (patch §nav): `"ns:action"` fires on press. `["tap","hold"]` resolves on release or at the threshold. `"none"` unbinds. A hold fires once.

## 3. Final controller matrix

20 inputs × 10 contexts = **200 cells**. Each cell gives the tap action and, after "·", the hold action. A cell without "·" fires on press. Rows are DECLARED in the patch, and the code path that IMPLEMENTS each cell is cited in §3.4.

Columns:
- **2D**: desktop-2d, uncaptured, cursor off.
- **Term·tmux**: desktop terminal with a tmux client.
- **Term·plain**: desktop terminal without tmux.
- **Cursor**: Start on, in 2D or a terminal.
- **Room**: desktop-3d camera mode, uncaptured.
- **Captured**: app-capture, game or vm-passthrough.
- **Half**: captured with the Guide+Select escape on.
- **Guide**: Guide held, in an uncaptured context with a compositor.
- **VT·tmux**: console with a tmux client.
- **VT·plain**: bare console.

A login prompt (getty or greeter) is deliberately **no function**. No user manager runs there, and the bridge releases the grab when another session is active (C-16).

### 3.1 Desktop contexts

| Input | 2D | Term·tmux | Term·plain | Cursor | Room |
|---|---|---|---|---|---|
| South | Enter · Shift+Enter [N I2] | Enter · S-Enter agent accept (mios-keys.tmux.conf:32) [N I2] | Enter · Shift+Enter [N I2] | as the context profile [F I2: dropped today] | jump; double = fly; hold = ascend [K] |
| East | Esc · Alt+Left [N I2] | Esc · Ctrl+C [N I2] | Esc · Ctrl+C [N I2] | as profile [F I2] | cancel: drop grab/resize, close picker, leave room cursor [N P1] |
| West | Tab · Shift+Tab [N I2] | Tab · BTab [N I2] | Tab · Shift+Tab [N I2] | as profile [F I2] | posture; hold = descend [K] |
| North | capture (tag) · fullscreen+capture [K] | same [K] | same [K] | same [K] | in-room capture · 2D fullscreen [K] |
| D-pad | arrows, repeat 400/60 ms [N I2] | arrows [N I2] | arrows [N I2] | arrows [F I2] | discrete move [K] (HAT; BTN_DPAD folded [F I4]) |
| Left stick | wheel Y / hwheel X, analog [N I2] | wheel (tmux mouse scroll); X ignored by tmux [N I2] | wheel (terminal scrollback) [N I2] | wheel [K] + hwheel [F I2] | walk [K] |
| Right stick | flick = focus window in that direction [N I2] | flick = tmux pane focus, window focus at the edge [N I2] | flick = focus window [N I2] | pointer [K] | look [K] |
| LT | previous tab (Ctrl+Shift+Tab), 500/250 [N I2] | tmux previous window (`C-b p`) [N I2] | scrollback page up (Shift+PgUp) [N I2] | right click [K] | resize (analog) [K] |
| RT | next tab (Ctrl+Tab), 500/250 [N I2] | tmux next window (`C-b n`) [N I2] | scrollback page down (Shift+PgDn) [N I2] | left click [K] | grab (analog) [K] |
| LB | ShaderGlass cycle [K] | ShaderGlass cycle [K] | ShaderGlass cycle [K] | ShaderGlass cycle [K] | CRT cycle [K] |
| RB | ShaderGlass toggle [K] | ShaderGlass toggle [K] | ShaderGlass toggle [K] | ShaderGlass toggle [K] | CRT toggle [K] |
| Select | Menu key (context menu) · bindings overlay [N I2] | session/window tree (`C-b w`) · bindings overlay [N I2] | none · bindings overlay [N I2] | as profile [F I2] | view cycle · bindings overlay [K/N P1] |
| Start | cursor on [K] | cursor on [K] | cursor on [K] | cursor off [K] | room cursor / camera [K] |
| Guide tap | app drawer [N I2] | app drawer [N I2] | app drawer [N I2] | app drawer [N I2] | app drawer [N P1] |
| L3 | new tab (Ctrl+T) [N I2] | tmux new window (`C-b t`) [N I2] | new MiOS terminal window [N I2] | as profile [N I2] | sprint toggle [N P1] |
| R3 | zoom reset (Ctrl+0) [N I2] | compact/auto view (`C-b f`) [N I2] | font size reset (Ctrl+0; Alacritty default, PLAUSIBLE) [N I2] | as profile [N I2] | recenter camera [N P1] |
| L3+R3 tap | enter room [K] | enter room [K] | enter room [K] | enter room [K] | exit room [K] |
| L3+R3 hold | nothing more (the tap already fired) | same | same | same | same |
| Touchpad (DualSense) | host touchpad: pointer, click, 2-finger scroll (libinput) [K] | same [K] | same [K] | same [K] | host pointer [K] |
| Motion sensors | none: not forwarded, no MiOS use [K] | none | none | none | none |

### 3.2 Owned and special contexts

| Input | Captured | Half | Guide | VT·tmux | VT·plain |
|---|---|---|---|---|---|
| South | app [K] | app [F I1] | toggle floating [K] | Enter (hold none: VT has no Shift+Enter) [N I3] | Enter [N I3] |
| East | app [K] | app [F I1] | close window [K] | Esc · Ctrl+C [N I3] | Esc · Ctrl+C [N I3] |
| West | app [K] | app [F I1] | terminal [K] | Tab · BTab [N I3] | Tab · BTab [N I3] |
| North | app [K] | app [F I1: re-captures today] | browser [K] | zoom pane (`C-b z`) [N I3] | none |
| D-pad | app [K] | app [F I1] | focus window l/r/u/d [N S0 on BTN_DPAD pads; I4 on HAT pads] | arrows [N I3] | arrows [N I3] |
| Left stick | app [K] | wheel + hwheel (host) [K/F I2] | Y = volume ±5 %, X = media previous/next [N I4] | Y = page up/down (copy mode), 500/250; X none [N I3] | arrows, 500/250 (operator C-18) [N I3] |
| Right stick | app [K] | pointer (host) [K] | flick = move window [N I4] | flick = pane focus, stops at the edge [N I3] | none (the pointer in cursor mode) |
| LT | app [K] | right click (host) [K] | move window to previous workspace [K; analog-only pads I4] | tmux previous window [N I3] | PageUp (operator C-18) [N I3] |
| RT | app [K] | left click (host) [K] | move window to next workspace [K; analog-only pads I4] | tmux next window [N I3] | PageDown (operator C-18) [N I3] |
| LB | app [K] | app [F I1: ShaderGlass today] | previous workspace [K] | previous tmux session [N I3] | BTab (operator C-18) [N I3] |
| RB | app [K] | app [F I1: ShaderGlass today] | next workspace [K] | next tmux session [N I3] | Tab (operator C-18) [N I3] |
| Select | app [K] | app; Guide+Select returns to full capture [K] | Guide+Select = cursor toggle / half-capture [K] | tree (`C-b w`) · keymap popup [N I3] | none |
| Start | app [K] | back to full capture [K] | system monitor [K; renamed I4] | copy mode (`C-b y`) · hold cursor mode [N I3] | cursor mode [N I3] |
| Guide tap | replayed to the app [K] | replayed to the app [F I1] | (modifier) | MiOS menu [N U1+I3] | none |
| L3 | app [K] | app [F I1] | lock [K] | tmux new window [N I3] | none |
| R3 | app [K] | app [F I1] | VM passthrough latch [K] | compact view (`C-b f`) [N I3] | none |
| L3+R3 tap | app (both presses forwarded) [K] | app [K] | suppressed: no lock+latch double fire [F I2] | switch to this user's graphical VT [N I3] | same [N I3] |
| L3+R3 hold | **release capture** (contract); also unsets a fullscreen the bridge set [K/F I1] | release capture [K] | suppressed [F I2] | nothing more | nothing more |
| Touchpad | host pointer into the focused app [K] | host pointer [K] | host pointer [K] | gpm pointer; reports reach tmux through the console bridge (C-18) [N I3] | gpm pointer (selection) |
| Motion sensors | none [K] | none | none | none | none |

VT·plain (operator decision C-18, I3): a bare VT may run any program (a shell, the session selector, a TUI), so it gets the keys every TUI reads: Enter, Esc/Ctrl+C, Tab/BTab, arrows and PageUp/PageDown, plus cursor mode and the VT switch. Linux has had no console scrollback since 5.9, so nothing pages the console itself; in a bare shell PageUp is `history-search-backward` (`/etc/inputrc:21`), and the left stick sends arrows, never PageUp.

### 3.3 Projection details

| Output | Mechanism |
|---|---|
| `key:` | The new uinput keyboard `mios-vctl-keyboard` (C-07). The udev rule classifies it as a keyboard before the `mios-vctl-*` catch-all, which sets LIBINPUT_IGNORE_DEVICE (70-mios-input.rules:6-7). Hyprland pins it to `kb_layout=us` with a per-device rule (C-31). The kernel console reads uinput keyboards too, so the same device drives a VT. |
| `pointer:` | The existing `mios-vctl-pointer`. REL_HWHEEL (6) is added to `&[0,1,8]` (linux.rs:337). |
| `tmux:key <k>` | `tmux -L <socket_name> send-keys -c <client_tty> -K <tmux_prefix> <k>`. `k` must be a key that the SSOT binds in the prefix table. |
| `tmux:focus <dir>` | `display -p -c <client> '#{pane_at_<dir>}'`. If it prints 1 on the desktop, run `hypr:movefocus <dir>`; otherwise send `-K <prefix> h/j/k/l`. |
| `tmux:page-up` / `page-down` | `copy-mode -eu -t <pane>` / `send-keys -X page-down` (only when the pane is in a mode). |
| `tmux:session-prev` / `next` | `switch-client -c <client> -p` / `-n`. |
| `tmux:menu` | The `mios-menu` command alias that U1 generates from the same items as MouseDown3 (mios-keys.tmux.conf:30-31), centred. |
| `tmux:keymap` | `display-popup -c <client> -E "/usr/libexec/mios/mios-inputd map --text \| less"`. |
| Client discovery (C-09) | On the desktop: a tmux client process under the focused terminal's pid, matched by `#{client_pid}`. On a VT: the client whose `client_tty` is `/dev/tty<active>`. If none is found, the `*_plain` profile applies. |
| `mios:graphical-vt` | Ctrl+Alt+F<VTNr> for this user's graphical session, VTNr from logind. |

### 3.4 Implementation map (IMPLEMENTED today, then the owner for new cells)

| Behavior | Code today | Owner for the change |
|---|---|---|
| North tap/hold capture | router.rs:367-371, 426-430, 696-701; runtime.rs:715-720 | I1 (capture_fullscreen_tag, release unset) |
| L3+R3 tap = room | router.rs:503-528 (uncaptured only) | I2 (C-13 Guide suppression, R-23) |
| L3+R3 hold = release | router.rs:703-712, 314-321 | I1 (C-02 release_tag) |
| Guide+Select half-capture | router.rs:441-446, 464-485 | I1 (C-01) |
| Guide system layer | router.rs:464-500; HAT, LS_*/RS_* and analog-trigger edges (nav_axis, layer_flicks) [landed I4] | I4 (HAT fold, LS/RS pseudo-keys) |
| LB/RB ShaderGlass | router.rs:351-366, gated by router.rs:553 | I1 (half-capture gate) |
| Room camera/move/grab/resize | router.rs:631-673 | P1 + I2 (room table) |
| Cursor pointer and clicks | router.rs:559-571, 715-757 | I2 (C-11) |
| Uncaptured 2D/terminal input | **forwarded to the vpad today** (router.rs:575-584, 589-594) | I2 (nav sink) |
| Guide tap uncaptured | **swallowed today** (router.rs:456-462) | I2 |
| VT | inputd follows the seat (logind state files) and projects to the VT [landed I3] | I3 |

## 4. Final keyboard map (global)

Unmarked rows are kept as they are. "Bypass" means the chord also reaches the host in app-capture and vm-passthrough.

| Chord | Action | Source (IMPLEMENTED) | Change |
|---|---|---|---|
| SUPER+Space, SUPER+R | app drawer | hyprland.lua:91-92; row app-drawer | bypass SUPER+CTRL+Space [K] |
| SUPER+Return, SUPER+Q | MiOS terminal | hyprland.lua:89-90; row terminal | bypass SUPER+CTRL+Return [K] |
| SUPER (tap) | search | hyprland.lua:97-98 | [K] |
| SUPER+c | close window | hyprland.lua:99 | [K] |
| SUPER+v | placement toggle | hyprland.lua:100 | [K] |
| SUPER+P / SUPER+J | pseudo / toggle split | hyprland.lua:101,104 | [K] |
| SUPER+F11 | fullscreen | hyprland.lua:102 | **bypass SUPER+CTRL+F11 [F S0, C-06]** |
| SUPER+SHIFT+F11 | maximize (not a capture) | hyprland.lua:103 | [K] |
| SUPER+arrows | focus | hyprland.lua:105-108 | [K] |
| SUPER+LMB / RMB | drag / resize | hyprland.lua:109-110 | [K] |
| SUPER+Escape | keymap overlay | hyprland.lua:111 | **bypass SUPER+CTRL+Escape [N S0, C-29]**; overlay generated from the global tables [F G1, C-25] |
| Print, SUPER+SHIFT+S / SHIFT+Print / ALT+Print / CTRL+Print | screenshot region / output / window / all | hyprland.lua:113-117 | **bypass SUPER+CTRL+Print for region [N G1]** |
| SUPER+G / +SHIFT / +ALT | ShaderGlass toggle / cycle / setup (in the room the plugin takes SHIFT and ALT: CRT toggle, picker) | hyprland.lua:129-131 | [K] |
| SUPER+1..0 / SUPER+SHIFT+1..0 | workspace / move to | hyprland.lua:133-136 (dynamic loop) | emitted literally so the gate audits them [F G1, C-21] |
| SUPER+F12 | Hypr3D room | hyprland.lua:141 | [K] |
| CTRL+ALT+SHIFT+t/a/g/m | terminal / AI / agents / system | hyprland.lua:93-96 | [K] |
| SUPER+CTRL+equal / minus / 0, SUPER+CTRL+wheel | session scale | hyprland.lua:239-253 | live in capture and VMs [F G1, C-36] |
| SUPER+CTRL+p / f / m | VM latch / VM fullscreen / Looking Glass command key | hyprland.lua:254-285 | [K] |
| tmux `C-b` + t a g m h j k l s v n p w z y d Tab b o f | as mios-keys.tmux.conf:10-29 | generated | [K]; no new prefix keys (mobile-safe rule, lib.rs:268) |
| tmux root S-Enter | agent accept | mios-keys.tmux.conf:32 | [K] |
| `reserved_terminal` | + CTRL+b, CTRL+s | inputd already enforces them (config.rs:328-336) | [F S0, C-24] |

## 5. Docs-drift fixes

| # | Where | Drift | Fix (in keybinds.md or the patch) |
|---|---|---|---|
| D-1 | keybinds.md:55 | "Outside VM/game contexts, LB/RB ShaderGlass": app-capture is excluded too (router.rs:553), and half-capture leaks today | "Uncaptured contexts only" (F-02) |
| D-2 | keybinds.md:56 | "SELECT cycles the 3D camera view": only in the room (router.rs:373-377) | Context-qualified matrix (F-27) |
| D-3 | keybinds.md:66-68 | "an ordinary game ... keeps both buttons": a 500 ms L3+R3 hold releases any captured app (router.rs:703-712), and outside the room fullscreen stays (runtime.rs:719) | Contract text plus the I1 fix (F-01) |
| D-4 | keybinds.md:40-45 | Partial shortcut list | Full keyboard table (F-06) |
| D-5 | keybinds.md:29 | Capture key names are missing | `capture_fullscreen`, `capture_tag`, `release_tag` (F-07) |
| D-6 | keybinds.md:100-105, 145 | Legacy recovery path | Rewritten for I1 (C-05) |
| D-7 | keybinds.md:28 | The context list lacks console | Adds `console` (landed I3) |
| D-8 | keybinds.md:58-61 | VT mouse via GPM implied | The VT is driven by key projection, and gpm reaches tmux through the console bridge (C-18, landed I3) |
| D-9 | keybinds.md:139 | Configurator scope | nav/room tables are TOML-only, listed by mios-controls |
| D-10 | mios.toml:4221 | Cites /var/tmp/mios-keybinds/schema.md | Points to keybinds.md (patch) (F-08..F-13) |
| D-11 | mios.toml:4364-4365 | Tier 2 "Reserved, not yet realised": it is realised (hyprland.lua:254-285) | Patch comment (F-14) |
| D-12 | mios.toml:4409-4411 | Tier-4 comment sits above `[keybindings.global.vm]` | Moved above `[keybindings.global.controller]` (F-14) |
| D-13 | mios.toml:4327-4328 | Session scale "reaches the host while ... a captured app owns the keyboard": false today (R-27) | Comment states the G1 condition |
| D-14 | mios.toml:8779-8782 | ShaderGlass "on the aimed/focused window": outside the room it is global and uncaptured only | Patch comment |
| D-15 | mios-gate keybinds.rs:2 | Cites schema.md | GT |
| D-16 | mios-sync-theme:93-99 | Hardcoded "L3 + R3" label and SUPER+G; the overlay is built from legacy tables (F-28) | G1 (C-25) |
| D-17 | router.rs:864, runtime.rs:780 test messages | "ordinary games keep both buttons" | I1 test rename |

## 6. Ownership-contract integration

Invariants. Each one is enforced by the listed change and tested in tests.md.

| INV | Statement | Enforced by |
|---|---|---|
| INV-1 | A captured window receives every controller input except the `toggle_chord` hold (release) and `mode_override_chord` (half-capture). In half-capture only the sticks and LT/RT become host pointer input. Every button, Guide included, still reaches the app, and Start or the chord returns to full capture. | C-01 (R-01) |
| INV-2 | Keyboard: in app-capture and vm-passthrough, exactly the chords on `bypass_mod` reach the host. | C-36 (R-27), hyprland.lua:319 |
| INV-3 | North takes ownership (tap = capture, hold = fullscreen+capture) wherever a compositor runs. A held toggle_chord is the only release, and it undoes a fullscreen that the bridge set. | C-02, existing router.rs:426-430, 696-712 |
| INV-4 | Capture state is compositor state (tags plus input_owner), read by **one** predicate in Lua and inputd. `release_tag` never demotes the vm-passthrough or game class contexts. | C-02, C-35 (R-02, R-03) |
| INV-5 | Exactly one axis sink per frame (App, Room, Nav or Pointer). | C-04 |
| INV-6 | On any change of context, profile, owner, Guide press or SYN_DROPPED: cancel tap/hold timers and auto-repeat, and release every held virtual-keyboard key. Nav state is per device. | C-08 (R-04) |
| INV-7 | One physical owner. No fallback process can drive both a game and the camera. | C-05 (R-05) |
| INV-8 | No input is synthesized before login, or while another user's session is active on the seat (the grab is released). | C-16 |
| INV-9 | A VM client's own capture state remains client-owned (mios.toml:4373). | unchanged |
| INV-10 | Every input × MiOS-owned context maps to an action or to an explicit `"none"`. The gate and mios-controls fail otherwise. | C-23 (GT) |

## 7. Implementation plan by owner

Order:
1. **S0**: SSOT-only, zero code.
2. **I1**: the live bugs.
3. **G1** with **GT**.
4. **I2** with **U1**: the nav grammar.
5. **I3**: VT.
6. **P1**.
7. **I4**.
8. **M1**.

Docs land with each step: the step's commit removes the step marker from keybinds.md.

### 7.1 SSOT-only (vendor `usr/share/mios/mios.toml`) — step S0, 5 changes

| ID | Change | Patch location |
|---|---|---|
| C-06 | `fullscreen.bypass = "SUPER+CTRL+F11"` | `[[keybindings.global.bindings]] id="fullscreen"` |
| C-29 | `keymap.bypass = "SUPER+CTRL+Escape"` | id="keymap" |
| C-24 | `reserved_terminal` + `"CTRL+b"`, `"CTRL+s"` (matches config.rs:328-336) | `[keybindings.global]` |
| C-15a | `system_layer` + `BTN_DPAD_LEFT/RIGHT/UP/DOWN = "hypr:movefocus <dir>"` (works for BTN_DPAD pads now; HAT pads at I4) | `[...controller.system_layer]` |
| C-26a | SSOT comment fixes D-10..D-14, plus consumer annotations on every hypr3d.controller key | Blocks A and B |

After S0: regenerate the projections (globals.sh/ps1, names registry, env baseline, hyprland.lua, as in ebece0e20). Then run `mios-gate keybind-collisions --root /` and `mios-inputd check`.

### 7.2 Docs — 1 change (17 fixes)

| ID | Change |
|---|---|
| C-26b | Replace usr/share/doc/mios/keybinds.md with `keybinds.md` from this directory. It applies D-1..D-9 and carries step markers. The gate header (D-15), the overlay (D-16) and the test strings (D-17) belong to their owners below. |

### 7.3 mios-inputd (tools/native/mios-inputd, plus its udev rule and user unit) — 19 changes

| Step | ID | Change | Files |
|---|---|---|---|
| I1 | C-01 | Half-capture per R-01 | router.rs:440-462, 553, 559-571 |
| I1 | C-02 | `input:window off` adds `release_tag`, removes `capture_tag` and unsets fullscreen if `capture_fullscreen_tag` is present. `on`/`fullscreen` remove `release_tag`. The template honours `release_tag` (not for class contexts). | runtime.rs:715-720, 77-86; config.rs:49-73, 215-218 |
| I1 | C-03 | `capture_override` stays pending until a snapshot confirms it or `context_stale_ms` passes; revert when the `input:window` child exits non-zero | router.rs:186-195, 303-313; runtime.rs emit |
| I1 | C-04 | One axis owner per frame, with a property test | router.rs:589-602, 631-673 |
| I1 | C-05 | Serve: exit 0 when disabled, idle with no seat or device access, non-zero on error. Drop recovery. Unit: `StartLimitIntervalSec=60`, `StartLimitBurst=5`. Remove `@drop:I1` keys and their LegacyConfig fields. | main.rs:82-93; config.rs:65, 251-253, 380-405; mios-inputd.service |
| I1 | C-27 | Tests: fixture `captured_system_layer=false` (config.rs:500); L3+R3 hold in `game`; RB/Guide+East/North/Start after the escape; D-17 messages | router.rs tests :776+, config.rs:500 |
| I2 | C-07 | `VirtualDevice::keyboard()` and the `key:` namespace. Udev: a `mios-vctl-keyboard` line with `ID_INPUT_KEYBOARD=1` before the catch-all. | linux.rs:328-338; runtime.rs emit; config.rs:367-378 (namespaces from SSOT); 70-mios-input.rules:6-7 |
| I2 | C-08 | Nav sink: profiles, `inherits`, `[tap, hold]`, repeat table, flicks, INV-6 flush, per-device state, R-23, R-26 | config.rs (new structs), router.rs (sink beside room()) |
| I2 | C-09 | Snapshot gains `tmux_client` (tty) for the focused terminal | runtime.rs:62-93 |
| I2 | C-30 | `tmux:` verbs (§3.3) replace the refusal at runtime.rs:224-226. Validate `tmux:key k` against the SSOT prefix keys. | runtime.rs:119-230 |
| I2 | C-11 | Cursor mode keeps button meanings (nav map), and adds hwheel | router.rs:559-571, 736-742; linux.rs:337 |
| I2 | C-13 | L3, R3 and L3+R3 disambiguation; Guide suppresses the chord | router.rs:502-528 |
| I2 | C-32 | `capture_osd` notification on capture and release | runtime.rs (action after `input:window`) |
| I3 | C-16 | `console` context: logind session TYPE, live Hyprland socket rediscovery (fixes the stale `HYPRLAND_INSTANCE_SIGNATURE`, runtime.rs:32-45, 94-100), release the grab when another session is active. Unit: drop `PartOf=`, `WantedBy=default.target`. CONTEXTS gets `console` with the R-09 scoped default. Guide hold disabled on the console (R-08). Landed. | runtime.rs; config.rs:8-16; mios-inputd.service:3,20 |
| I3 | C-17 | `mios:graphical-vt` (landed) | runtime.rs |
| I3 | C-18 | `console-bridge` pty wrapper, gpm/evdev -> SGR 1006 while mouse mode is on (operator decision; landed) | console.rs; mios-terminal |
| I4 | C-15b | HAT folded into BTN_DPAD_*; LS_*/RS_* pseudo-keys (resolver `button_code`); analog LT/RT edges; `hypr:movewindow`; volume and media argv. Landed. | router.rs:486-500, 589-594; runtime.rs:127-138; mios-resolver keybindings.rs:114-135 |
| I4 | C-19 | Rename `quickshell:monitor-toggle` to `mios:system-monitor`. Landed. | config.rs:136, 675; SSOT `#@I4` lines |
| I4 | O-7 | Per-driver quirk table (`controller.quirks`), applied at device open and inverted for the clone; fixture layouts xpad, xpad-dpad-buttons, dualsense. Landed. | config.rs (Quirk), linux.rs (apply_quirks, physical_events) |
| I4 | agent | The agent virtual controller (`vpad`, `controller.agent_pad`, [verbs.virtual_controller]) and nested-session controls passthrough (`bridge --nested`, registry skip). Landed. | vpad.rs, nested.rs, runtime.rs, main.rs |
| P1 | C-14b | Room table: whitelist `cancel`, `sprint`, `recenter`, `view` | runtime.rs:139-156; router.rs:373-395 |
| pre-I2 (xpad) | C-28 | Run `mios-inputd observe` on an xpad pad. Add `positional_fixups` only if physical Y reports BTN_WEST. | linux.rs device open |

### 7.4 Hypr3D plugin (usr/share/mios/hyprland/patches/) — 1 change

| Step | ID | Change |
|---|---|---|
| P1 | C-14a | Lua functions `hl.plugin.hypr3d.cancel()`, `sprint()`, `recenter()` next to `input_owner`/`input_capture` (hypr3d-mios-pip-theatre.patch:940-950). `view` already exists (runtime.rs:143). |

### 7.5 mios-gen / mios-unit-gen — 8 changes

| Step | ID | Change | Files |
|---|---|---|---|
| G1 | C-36 | A handle whose chord starts with `bypass_mod` gets `escape=true` (R-27) | global_bindings.rs:49-53 |
| G1 | C-35 / C-02 | One capture predicate `MIOS_INPUT_POLICY.captured(w)`, with input_owner and release_tag. `context()` uses it. | global_bindings.rs (the generator of hyprland.lua:302-312) |
| G1 | C-22 | Emit the dispatch bind only when no compositor bind with the same canonical chord exists; delete the 7-id list. This unblocks the `#@G1` screenshot row. | global_bindings.rs:53 |
| G1 | C-21 | Emit the workspace loop as 20 literal binds | render.rs (the loop at hyprland.lua:133-136) |
| G1 | C-25 | Generate the Super+Escape overlay from `[keybindings.global]`, the nav/room tables and system_layer; read toggle_chord | usr/libexec/mios/mios-sync-theme:77-103; Keymap.qml |
| G1 | C-31 | Per-device rule: `mios-vctl-keyboard` uses `kb_layout=us` | render.rs (hyprland input section) |
| U1 | C-10' | Emit `set -s command-alias[N] 'mios-menu=display-menu -T MiOS -x C -y C …'` from the MouseDown3 items (`menu_alias`) | mios-unit-gen lib.rs:280-305 |
| U1 | C-18 | Optional, PLAUSIBLE: `mouse_terms` emits `set -as terminal-features ',linux:mouse'` | mios-unit-gen lib.rs:193 |

### 7.6 miosd sessions / TUIs (mios-controls) and mios-gate — 5 changes

| Step | ID | Change | Files |
|---|---|---|---|
| M1 | C-33 | Session selector: Tab (West) = new session (`n`), so a controller can reach it. Up/Down/Enter/Esc are already covered (sessions.rs:477-495). | src/mios-rs/miosd/src/sessions.rs:476-496, help line :461 |
| GT | C-34 | mios-controls: CONTEXTS + console; `list` prints nav, room and hypr3d tables; `check` audits the **resolved user projection** (R-11d); tui.rs:809 reads `desktop.hyprland.hypr3d.controller.toggle_chord` (then `@drop:GT`) | tools/native/mios-controls/src/{validate.rs,tui.rs,model.rs} |
| GT | C-23 | Controller audit: every input × MiOS-owned context resolves to an action or `"none"`; namespaces are valid; console rows use console-capable namespaces only (key:, tmux:, mios:graphical-vt); TOGGLE_CHORD, mode_override_chord and room buttons do not overlap | mios-gate keybinds.rs:149-155, 242-279; mios-controls validate.rs:107-145 |
| GT | C-16g | The gate accepts `console` with the scoped default (R-09) | keybinds.rs:12-19, 55-57, 244, 292, 344, 364 |
| GT | C-26c | The gate header cites keybinds.md (D-15) | keybinds.rs:2 |

**Totals: 39 changes.** SSOT-only 5, docs 1, mios-inputd 19, Hypr3D plugin 1, mios-gen/unit-gen 8, sessions/TUIs/gate 5.

## 8. Operator decisions

| ID | Decision | Default | To change it |
|---|---|---|---|
| O-1 | Uncaptured `desktop-2d` uses the nav projection instead of forwarding to the vpad (behavior change). A windowed, unlisted game then needs one North tap. | nav on | `[keybindings.global.controller.nav.contexts] desktop-2d = "vpad"` |
| O-2 | Remember a North capture per window class | off | future `[...controller] remember_capture = true` |
| O-3 | Key projection into VM clients that have no gamepad channel (Looking Glass, RDP) | off | — |
| O-4 | Add `steam` to `[keybindings.global.contexts].game` | not added | edit that array (note: arrays replace) |
| O-5 | Auto-detect apps that read the vpad (/proc/<pid>/fd), PLAUSIBLE | off | — |
| O-6 | VT West hold = BTab | **on (I3)**: the installed keymaps map Shift+Tab to Meta_Tab (ESC TAB = linux kcbt) | `BTN_WEST = ["key:TAB","key:SHIFT+TAB"]` in both console profiles |
| O-7 | Hardware-agnostic mapping: evdev codes plus a per-driver quirk table in the SSOT | **on (I4)**: vendor quirk `xpad` (face order, dpad_to_buttons), proven with xpad/DualSense uinput fixtures; T-C28 on a physical Xbox pad still confirms it | edit `[keybindings.global.controller.quirks.<name>]` |

## 9. Live-bug traceability

| Live bug | Root path (verified) | Fixed by |
|---|---|---|
| RB fires ShaderGlass while an app is captured | (a) Half-capture: router.rs:440 sets captured=false and router.rs:553 runs legacy_action. (b) After a held L3+R3 the Lua still sees fullscreen==2 (hyprland.lua:305) while inputd has released it (router.rs:314-321, 188). (c) The app was never captured. | C-01; C-02 + C-35; O-1 + North tap + C-32 OSD |
| Left stick moves game and camera | No healthy native path does both. Paths that can: the legacy recovery daemon (main.rs:91-92), or a transient override drop on focus change | C-05; C-03; C-04 |
| VT mouse (gpm) does not reach tmux | inputd does not run without the desktop (mios-inputd.service:3,20); tmux mouse on TERM=linux is PLAUSIBLE | C-16 + C-07 + C-30, and the C-18 console bridge (landed I3) |
