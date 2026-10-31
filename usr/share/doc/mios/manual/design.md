<!-- AI-hint: Manual pages distilled from the source comments of design, sanitized, each passage anchored to the comment it came from. -->

# design

### MERGE NOTE

MERGE NOTE: the shipped table also carries the tmux-os keys that landed after ebece0e20 (ca999d74d:
extended_feature_slots, command_alias_*, launcher_key, the menu notes and sizes, the messages and the full
bindings list). Keep them from the shipped SSOT when merging BLOCK A; this text lists only the keys the bindings
contract reads. menu_key is one: the controller's tmux:menu verb is client-exact through it
(send-keys -c <client> -K <tmux_prefix> <menu_key>), and the gate rejects tmux:menu without it.

<!-- mios-src:c26c62d9cdab from docs/design/mios-global-bindings-staged.patch.toml:109-113 -->

### Global input contract (/usr/share/doc/mios/keybinds.md) ---...

--- Global input contract (/usr/share/doc/mios/keybinds.md) ---
One audited map of every host keyboard/controller binding across the four tiers: 1 desktop/window manager,
2 VM / Looking Glass / RDP / kernel VT, 3 terminal / POSIX / agent loop, 4 controller. `mios-gate
keybind-collisions` fails on a chord bound twice in intersecting contexts, a multi-step prefix clash, a
bypass that does not use bypass_mod, a host bind that leaks into vm-passthrough, a reserved POSIX terminal
chord stolen from the focused terminal, and a `realized_by = "hyprland"` binding the compositor does not have.
mios-inputd (Rust) consumes the same tables; both normalise chords to the vectors in
usr/share/mios/keybinds/chord-vectors.json. POSIX/Readline defaults stay with the focused application.
Ownership rule: the focused window is either captured (the app owns every keyboard handle except escapes, and every
controller input except a held L3+R3 and Guide+Select) or uncaptured (MiOS owns the controller).

<!-- mios-src:066d9c58acd8 from docs/design/mios-global-bindings-staged.patch.toml:144-153 -->

### Tier 4

Tier 4: the controller. One physical bridge (mios-inputd) owns every pad. Captured: the app gets everything through
the virtual pad except a held toggle_chord (release, capture_hold_ms) and mode_override_chord (half-capture: the
sticks and triggers drive the host mouse; every button still goes to the app; Start or the chord again returns).
Uncaptured: unmodified buttons follow the nav profile of the context (I2) and the room map ([desktop.hyprland.hypr3d.
controller], [keybindings.global.controller.room]). With Guide held, system_layer (no leak to the app).

<!-- mios-src:d8ee12d0db1d from docs/design/mios-global-bindings-staged.patch.toml:372-376 -->

### @I4 LS_UP = "mios:volume up" @I4 LS_DOWN = "mios:volume...

@I4 LS_UP = "mios:volume up"
@I4 LS_DOWN = "mios:volume down"
@I4 LS_LEFT = "mios:media previous"
@I4 LS_RIGHT = "mios:media next"
@I4 RS_LEFT = "hypr:movewindow left"
@I4 RS_RIGHT = "hypr:movewindow right"
@I4 RS_UP = "hypr:movewindow up"
@I4 RS_DOWN = "hypr:movewindow down"

<!-- mios-src:dd0cbc6f635c from docs/design/mios-global-bindings-staged.patch.toml:422-429 -->

### Navigation projection

--- Navigation projection: what an uncaptured controller does outside the room (I2; console I3) ---
Value grammar, the same for every profile:
  "ns:action"        fires on press. It auto-repeats only if the input has a [nav.repeat] entry.
  ["tap", "hold"]    tap fires on release before capture_hold_ms. Hold fires once at capture_hold_ms and never repeats.
  "none"             does nothing. This is the only unbind word; "" is rejected (an empty string never overrides).
Arrays replace atomically across tiers, so an override always restates both halves.
Inputs: BTN_SOUTH/EAST/WEST/NORTH, BTN_TL/TR (bumpers), BTN_TL2/TR2 (triggers, digital at trigger_threshold),
  BTN_SELECT/START, BTN_MODE (Guide TAP: released before capture_hold_ms with nothing else pressed), BTN_THUMBL/R
  (ignored while their own stick is deflected), BTN_DPAD_* (HAT folded in), LS_*/RS_* (stick directions; RS fires one
  flick per deflection), TOGGLE_CHORD (the configured toggle_chord).
Not in nav (contract or room owned): BTN_NORTH capture, BTN_TL/TR ShaderGlass, BTN_START cursor mode, the
  toggle_chord room toggle and hold-release. A console profile may claim them, because no compositor runs there.
Namespaces: key: = the mios-vctl-keyboard virtual keyboard, pinned to the "us" layout. pointer: = analog wheel.
  tmux: = commands for the tmux client in the focused terminal or VT on socket_name: key <k> (prefix-table key k),
  focus <dir> (falls through to hypr:movefocus at the window edge), page-up, page-down, session-prev, session-next,
  menu, keymap.
@I2 [keybindings.global.controller.nav]
@I2 enabled = true
@I2 flick_on = 0.6                      # right-stick radial deflection (after stick_deadzone) that fires one flick
@I2 flick_off = 0.3                     # the stick must fall below this before it can flick again
@I2
@I2 # Context -> profile. A table merges per key, so a higher tier changes one context without restating the rest.
@I2 # "vpad" forwards the pad to the virtual gamepad instead (the behaviour before nav; operator decision O-1).
@I2 [keybindings.global.controller.nav.contexts]
@I2 desktop-2d = "desktop"
@I2 terminal = "terminal"               # terminal_tmux when a tmux client runs under the focused terminal, else terminal_plain
@I3 console = "console"                 # console_tmux when a tmux client is attached to the active VT, else console_plain
@I2
@I2 # input -> [delay_ms, rate_ms]. Inputs not listed never repeat. Hold actions never repeat.
@I2 [keybindings.global.controller.nav.repeat]
@I2 BTN_DPAD_UP = [400, 60]
@I2 BTN_DPAD_DOWN = [400, 60]
@I2 BTN_DPAD_LEFT = [400, 60]
@I2 BTN_DPAD_RIGHT = [400, 60]
@I2 BTN_TL2 = [500, 250]
@I2 BTN_TR2 = [500, 250]
@I2 LS_UP = [500, 250]                  # discrete actions only (VT paging); pointer: wheel is analog and ignores it
@I2 LS_DOWN = [500, 250]
@I2
@I2 [keybindings.global.controller.nav.desktop]
@I2 BTN_SOUTH = ["key:ENTER", "key:SHIFT+ENTER"]
@I2 BTN_EAST = ["key:ESC", "key:ALT+LEFT"]
@I2 BTN_WEST = ["key:TAB", "key:SHIFT+TAB"]
@I2 BTN_DPAD_UP = "key:UP"
@I2 BTN_DPAD_DOWN = "key:DOWN"
@I2 BTN_DPAD_LEFT = "key:LEFT"
@I2 BTN_DPAD_RIGHT = "key:RIGHT"
@I2 LS_UP = "pointer:wheel-up"
@I2 LS_DOWN = "pointer:wheel-down"
@I2 LS_LEFT = "pointer:hwheel-left"
@I2 LS_RIGHT = "pointer:hwheel-right"
@I2 RS_LEFT = "hypr:movefocus left"
@I2 RS_RIGHT = "hypr:movefocus right"
@I2 RS_UP = "hypr:movefocus up"
@I2 RS_DOWN = "hypr:movefocus down"
@I2 BTN_TL2 = "key:CTRL+SHIFT+TAB"
@I2 BTN_TR2 = "key:CTRL+TAB"
@I2 BTN_SELECT = ["key:COMPOSE", "quickshell:keymap-toggle"]
@I2 BTN_MODE = "quickshell:drawer-toggle"
@I2 BTN_THUMBL = "key:CTRL+t"
@I2 BTN_THUMBR = "key:CTRL+0"
@I2
@I2 [keybindings.global.controller.nav.terminal_tmux]
@I2 inherits = "desktop"
@I2 BTN_EAST = ["key:ESC", "key:CTRL+c"]
@I2 RS_LEFT = "tmux:focus left"
@I2 RS_RIGHT = "tmux:focus right"
@I2 RS_UP = "tmux:focus up"
@I2 RS_DOWN = "tmux:focus down"
@I2 BTN_TL2 = "tmux:key p"
@I2 BTN_TR2 = "tmux:key n"
@I2 BTN_SELECT = ["tmux:key w", "quickshell:keymap-toggle"]
@I2 BTN_THUMBL = "tmux:key t"
@I2 BTN_THUMBR = "tmux:key f"
@I2
@I2 [keybindings.global.controller.nav.terminal_plain]
@I2 inherits = "desktop"
@I2 BTN_EAST = ["key:ESC", "key:CTRL+c"]
@I2 BTN_TL2 = "key:SHIFT+PAGEUP"
@I2 BTN_TR2 = "key:SHIFT+PAGEDOWN"
@I2 BTN_SELECT = ["none", "quickshell:keymap-toggle"]
@I2 BTN_THUMBL = "exec:mios-terminal"
@I3
@I3 [keybindings.global.controller.nav.console_tmux]
@I3 inherits = "terminal_tmux"
@I3 BTN_SOUTH = "key:ENTER"              # the VT sends the same byte for Shift+Enter
@I3 BTN_WEST = "key:TAB"                 # BTab on the VT keymap is unverified (showkey -a)
@I3 LS_UP = "tmux:page-up"
@I3 LS_DOWN = "tmux:page-down"
@I3 LS_LEFT = "none"
@I3 LS_RIGHT = "none"
@I3 BTN_NORTH = "tmux:key z"
@I3 BTN_TL = "tmux:session-prev"
@I3 BTN_TR = "tmux:session-next"
@I3 BTN_SELECT = ["tmux:key w", "tmux:keymap"]
@I3 BTN_START = "tmux:key y"
@I3 BTN_MODE = "tmux:menu"
@I3 TOGGLE_CHORD = "mios:graphical-vt"
@I3
@I3 [keybindings.global.controller.nav.console_plain]   # a bare VT shell: only keys that are safe in any program
@I3 BTN_SOUTH = "key:ENTER"
@I3 BTN_EAST = ["key:ESC", "key:CTRL+c"]
@I3 BTN_WEST = "key:TAB"
@I3 BTN_DPAD_UP = "key:UP"
@I3 BTN_DPAD_DOWN = "key:DOWN"
@I3 BTN_DPAD_LEFT = "key:LEFT"
@I3 BTN_DPAD_RIGHT = "key:RIGHT"
@I3 LS_UP = "none"                       # Linux >= 5.9 has no console scrollback
@I3 LS_DOWN = "none"
@I3 LS_LEFT = "none"
@I3 LS_RIGHT = "none"
@I3 RS_LEFT = "none"
@I3 RS_RIGHT = "none"
@I3 RS_UP = "none"
@I3 RS_DOWN = "none"
@I3 BTN_TL2 = "none"
@I3 BTN_TR2 = "none"
@I3 BTN_TL = "none"
@I3 BTN_TR = "none"
@I3 BTN_NORTH = "none"
@I3 BTN_SELECT = "none"
@I3 BTN_START = "none"
@I3 BTN_MODE = "none"
@I3 BTN_THUMBL = "none"
@I3 BTN_THUMBR = "none"
@I3 TOGGLE_CHORD = "mios:graphical-vt"

<!-- mios-src:b5e733d80538 from docs/design/mios-global-bindings-staged.patch.toml:431-556 -->
