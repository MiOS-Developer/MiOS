<!-- AI-hint: Global input SSOT, ownership contract, final controller and keyboard map, native Rust port and verification. -->
<!-- AI-related: usr/share/mios/mios.toml [keybindings.global], tools/native/mios-inputd, tools/native/mios-resolver/src/keybindings.rs, src/mios-rs/mios-gate/src/keybinds.rs, tools/native/mios-controls -->
# MiOS global input bindings

`/usr/share/mios/mios.toml` declares the vendor map under `[keybindings]` and
`[keybindings.global]`, and the room map under `[desktop.hyprland.hypr3d.controller]`.
The native resolver merges these layers in order:
1. vendor fragments;
2. the host `/etc/mios/mios.toml` and its fragments;
3. the user's `~/.config/mios/mios.toml` and its fragments.

An empty user file keeps the vendor defaults. Merging follows two rules:
- Tables merge key by key.
- **Arrays replace.** A tier that declares one `[[keybindings.global.bindings]]` row,
  or sets `reserved_terminal`, replaces the whole vendor array. Restate every entry
  you want to keep.

An empty string never overrides a value. To unbind a controller input, write `"none"`.

Desktop keyboard bindings remain native Hyprland bindings. The input daemon never
grabs keyboards and never replaces terminal line discipline.

**Status markers.** A row marked `[step X]` is declared in the SSOT patch but not yet
implemented. The commit that implements it removes the marker. Unmarked rows describe
shipped behavior.

## 1. The ownership contract

One rule decides who receives input. **The focused window is either captured, so the
app owns the input, or uncaptured, so MiOS owns it.**

- **Capture.** Any of these captures a window:
  - native fullscreen (`capture_fullscreen = true`, fullscreen mode 2);
  - the window tag `capture_tag = "mios-input-captured"`;
  - the Hypr3D room's input owner.

  The window classes in `[keybindings.global.contexts]` select the `game` and
  `vm-passthrough` contexts, which are captured too.
- **Take ownership: North.** A tap captures the aimed or focused window. A hold of
  `capture_hold_ms` (500 ms) makes it fullscreen and captures it. In the room, a tap
  gives the aimed window all input, and a hold enters 2D fullscreen.
- **Release: hold L3+R3** (`toggle_chord`) for `capture_hold_ms`. This is the only release.
  - The release is stored on the window as `release_tag = "mios-input-released"`.
  - If the bridge made the window fullscreen, the release also un-fullscreens it.
  - A release cancels only fullscreen, tag or room capture. It never turns a
    `vm-passthrough` or `game` window into a desktop window. [step I1]
  - Today the release removes only the tag and leaves fullscreen set.
- **While captured, the controller belongs to the app.** Every button, stick, trigger and
  Guide press reaches it through the `mios-vctl-*` virtual gamepad. A Guide tap is
  replayed to the app. There are two exceptions:
  - the L3+R3 hold (release);
  - `mode_override_chord` (Guide+Select), which enters **half-capture**:
    - the right stick moves the host pointer;
    - the left stick scrolls;
    - LT and RT right-click and left-click;
    - every button, Guide included, still goes to the app [step I1];
    - Start or Guide+Select returns to full capture.

  Today half-capture also runs Guide combinations and LB/RB on the host. That is a known
  bug, and step I1 fixes it.
- **While captured, the keyboard belongs to the app.** Only the chords on
  `bypass_mod = "SUPER+CTRL"` reach the host. Today that covers the declared bypass
  chords and the VM escape handles. The session-scale chords join them in step G1.
- **Notice.** Every capture and release shows "Controller -> <app>. Hold L3+R3 to
  release." [step I2]
- A VM client's own keyboard/mouse capture state stays owned by the client.
- Before login, at a greeter, or while another user's session is active on the seat,
  MiOS synthesizes no input [step I3].

## 2. Contexts

| Context | Selected when |
|---|---|
| `desktop-2d` | uncaptured GUI window |
| `terminal` | uncaptured `mios-terminal`, Alacritty, kitty or foot |
| `desktop-3d` | the Hypr3D room is active and no window owns input |
| `app-capture` | fullscreen, `capture_tag` or room input owner |
| `game`, `vm-passthrough` | class lists in `[keybindings.global.contexts]` |
| `console` | no compositor, or this user's active seat session is a tty [step I3] |

Cursor mode (Start) and the Guide layer are **modes** on top of a context, not contexts.
SSH is not an input context. A remote user drives tmux with `Ctrl-b` and one key.

## 3. Controller

The same control means the same thing everywhere MiOS owns input:

| Control | Tap | Hold (500 ms) |
|---|---|---|
| South | confirm (Enter) | Shift+Enter, the agent accept chord |
| East | back (Esc) | stronger back: Ctrl+C in terminals, Alt+Left in GUIs |
| West | next field (Tab) | Shift+Tab |
| North | capture | fullscreen + capture |
| D-pad | arrows, repeating | |
| Right stick | one flick = focus in that direction | |
| Left stick | scroll | |
| LT / RT | previous / next tab, window or page | |
| LB / RB | ShaderGlass cycle / toggle (CRT in the room) | |
| Select | context menu or tree | bindings overlay |
| Start | cursor mode: the sticks and triggers become a mouse, buttons keep their meaning | |
| Guide | app drawer (VT: MiOS menu) | modifier for the system layer |
| L3 / R3 | new tab or window / reset view | |
| L3+R3 | enter or leave the room (VT: back to the desktop) | release capture |

A tap fires when you release the button before 500 ms. A hold fires once at 500 ms and
never repeats. An L3 or R3 click is ignored while its own stick is deflected, and an
overlap of L3 and R3 cancels both single clicks [step I2].

### 3.1 Desktop

Unless marked otherwise, every cell in the 2D, terminal and Cursor columns is [step I2].
Today those inputs go to the virtual gamepad, and in cursor mode the buttons are dropped.

| Input | 2D app | Terminal with tmux | Terminal without tmux | Cursor mode | Hypr3D room |
|---|---|---|---|---|---|
| South | Enter · Shift+Enter | Enter · agent accept | Enter · Shift+Enter | as the context | jump; double = fly; hold = up |
| East | Esc · Alt+Left | Esc · Ctrl+C | Esc · Ctrl+C | as the context | cancel grab/picker/room cursor [step P1] |
| West | Tab · Shift+Tab | Tab · Shift+Tab | Tab · Shift+Tab | as the context | posture; hold = down |
| North | capture · fullscreen | capture · fullscreen | capture · fullscreen | capture · fullscreen | room capture · 2D fullscreen |
| D-pad | arrows | arrows | arrows | arrows | move |
| Left stick | scroll (and sideways) | scroll | scrollback | scroll (shipped) | walk |
| Right stick | focus window | focus pane, then window at the edge | focus window | pointer (shipped) | look |
| LT | previous tab | previous tmux window | page up | right click (shipped) | resize |
| RT | next tab | next tmux window | page down | left click (shipped) | grab |
| LB | ShaderGlass cycle (shipped) | same | same | same | CRT cycle |
| RB | ShaderGlass toggle (shipped) | same | same | same | CRT toggle |
| Select | context menu · overlay | session tree · overlay | none · overlay | as the context | camera view (shipped) · overlay [step P1] |
| Start | cursor on (shipped) | cursor on | cursor on | cursor off | room cursor/camera |
| Guide tap | app drawer | app drawer | app drawer | app drawer | app drawer [step P1] |
| L3 | new tab (Ctrl+T) | new tmux window | new terminal window | as the context | sprint [step P1] |
| R3 | zoom reset (Ctrl+0) | compact/auto view | font size reset | as the context | recenter [step P1] |
| L3+R3 tap | enter the room (shipped) | same | same | same | leave the room |

DualSense touchpad: libinput drives it as an ordinary touchpad in every graphical context,
including captured apps, where it acts like any mouse. MiOS does not bind it. The motion
sensors are not forwarded.

### 3.2 Captured, Guide layer and VT

| Input | Captured app | Half-capture | Guide held (desktop) | VT with tmux [step I3] | VT without tmux [step I3] |
|---|---|---|---|---|---|
| South | app | app [step I1] | toggle floating | Enter | Enter |
| East | app | app [step I1] | close window | Esc · Ctrl+C | Esc · Ctrl+C |
| West | app | app [step I1] | terminal | Tab | Tab |
| North | app | app [step I1] | browser | zoom pane | none |
| D-pad | app | app | focus window (HAT d-pads [step I4]) | arrows | arrows |
| Left stick | app | scroll | volume (Y), previous/next track (X) [step I4] | page up/down | none |
| Right stick | app | pointer | move window [step I4] | focus pane | none |
| LT / RT | app | right / left click | move window to previous/next workspace | previous/next tmux window | none |
| LB / RB | app | app [step I1] | previous/next workspace | previous/next tmux session | none |
| Select | app | app | Guide+Select = cursor / half-capture | session tree · bindings popup | none |
| Start | app | back to full capture | system monitor | copy mode | none |
| Guide tap | replayed to app | replayed to app [step I1] | (modifier) | MiOS menu | none |
| L3 / R3 | app | app [step I1] | lock / VM passthrough latch | new tmux window / compact view | none |
| L3+R3 tap | app | app | suppressed [step I2] | back to the graphical VT | back to the graphical VT |
| L3+R3 hold | release capture | release capture | suppressed [step I2] | | |

On a VT:
- **Shift+Enter cannot be sent.** The console encodes it like Enter, so South has no hold there.
- **The Guide layer does nothing**, because there is no compositor.
- **The keys are pressed by a virtual keyboard**, pinned to the US layout.
- **tmux actions are tmux commands, not keystrokes.** They run against the tmux client on
  that VT, so a bare shell never receives a stray `Ctrl-b`.

### 3.3 Where the controller map lives

| Table | What |
|---|---|
| `[keybindings.global.controller]` | Guide button, half-capture chord, cursor mode, capture keys, timing, pointer |
| `[keybindings.global.controller.system_layer]` | Guide-held actions |
| `[keybindings.global.controller.nav]` and `.nav.contexts`, `.nav.repeat`, `.nav.<profile>` | uncaptured 2D, terminal and VT behavior [step I2/I3] |
| `[keybindings.global.controller.room]` | room additions [step P1] |
| `[desktop.hyprland.hypr3d.controller]` | room camera, movement, grab/resize, CRT, face buttons, toggle chord |

How a nav value is written:
- `"ns:action"` fires on press.
- `["tap", "hold"]` gives a tap and a hold. Always restate both halves.
- `"none"` unbinds.

Profiles may say `inherits = "<profile>"`. Context-to-profile is a table, so a single
line reverts one context. For example, `desktop-2d = "vpad"` sends the pad to the virtual
gamepad in ordinary windows, as before step I2.

Inputs: `BTN_*`, `BTN_DPAD_*`, `BTN_TL2`/`BTN_TR2`, `LS_*`/`RS_*` and `TOGGLE_CHORD`.

Namespaces: `key:`, `pointer:`, `tmux:`, `hypr:`, `quickshell:` and `mios:`.

The configurator at `/usr/share/mios/configurator/mios.html` edits the global binding map
and the system layer. The nav and room tables are TOML-only. `mios-controls list` shows
them [step GT].

## 4. Keyboard

| Chord | Action |
|---|---|
| Super+Space, Super+R | app drawer (bypass Super+Ctrl+Space) |
| Super+Return, Super+Q | MiOS terminal (bypass Super+Ctrl+Return) |
| Super (tap) | search |
| Super+C | close window |
| Super+V | placement toggle |
| Super+P / Super+J | pseudo-tile / toggle split |
| Super+F11 | fullscreen (bypass Super+Ctrl+F11, which un-fullscreens a captured app) |
| Super+Shift+F11 | maximize (not a capture) |
| Super+arrows, Super+left/right mouse drag | focus, move, resize |
| Super+Escape | keymap overlay (bypass Super+Ctrl+Escape) |
| Super+G, Super+Shift+G, Super+Alt+G | ShaderGlass toggle, cycle, setup (in the room: CRT toggle and picker) |
| Super+1..0, Super+Shift+1..0 | workspace, move to workspace |
| Super+F12 | Hypr3D room |
| Print, Super+Shift+S / Shift+Print / Alt+Print / Ctrl+Print | screenshot region / output / window / all (bypass Super+Ctrl+Print [step G1]) |
| Ctrl+Alt+Shift+T / A / G / M | terminal / AI / agents / system monitor |
| Super+Ctrl+= / - / 0, Super+Ctrl+wheel | session scale (live over captured apps and VMs [step G1]) |
| Super+Ctrl+P / F / M | VM passthrough latch / VM fullscreen / Looking Glass command key (held) |
| tmux `Ctrl-b` + t a g m h j k l s v n p w z y d Tab b o f | new window, AI, agents, monitor, pane focus, split, next/previous window, tree, zoom, copy mode, detach, BTab, send prefix, next workspace pane, compact view |
| tmux Shift+Enter | agent accept |

tmux prefix keys stay mobile-safe: Tab or one lowercase letter. POSIX signals, Readline
editing and the tmux prefix belong to the terminal. `reserved_terminal` lists the chords
that no host binding may take there, including `Ctrl+B` (tmux prefix) and `Ctrl+S` (flow
control). Do not turn either into a global host interception.

## 5. Binding data and normalization

Each `[[keybindings.global.bindings]]` entry has these fields:
- a unique `id`;
- `tier`, from 1 to 4;
- `chord`;
- a namespaced `action`;
- optional `contexts`, `passthrough`, `bypass`, `scope` and `note`.

A missing `scope` means `host`. `scope = "app"` documents an application-owned shortcut
and never installs a host interception. Tiers are metadata:
1. desktop and window manager;
2. VM and remote clients;
3. terminal;
4. controller.

Literal compositor binds are audited without a row. Add a row only to give a chord a
bypass or contexts.

`tools/native/mios-resolver/src/keybindings.rs` owns chord normalization for both the
daemon and the gate:
- Keyboard modifiers are ordered `SUPER`, `CTRL`, `ALT`, `SHIFT`, then side-specific
  `LEFTCTRL` / `RIGHTCTRL`. `RIGHTCTRL` never collapses into `CTRL`.
- Keyboard names normalize to lower case.
- Controller names keep their `BTN_*` spelling and are sorted. Aliases normalize to the
  same physical code. `BTN_X` is `BTN_NORTH` and `BTN_Y` is `BTN_WEST`, so on drivers
  that are not positional, check the physical layout with `observe`.
- Multi-step sequences use `chord_sep`.

The shared test vectors are in `/usr/share/mios/keybinds/chord-vectors.json`.

`action_argv` holds direct executable argument vectors. The gate requires every
`quickshell:` and `mios:` action to have an entry there or in `planned_actions`.
`planned_actions` never dispatches. `native_actions` declares the Looking Glass hold
handler that Hyprland realizes synchronously. Chord release, focus loss and configuration
reload release the held key.

The compositor projection owns its own binding handles. Ordinary host bindings yield in
VM and app-capture contexts, and bypasses use `dont_inhibit` and `submap_universal`.
VM commands revalidate address, PID and class in the compositor transaction:
- Looking Glass uses the configured escape keysym.
- X11 FreeRDP uses its native Right Ctrl escape.
- Other clients get host fullscreen without guessing their capture protocol.

A sent escape is reported as `native_command_sent`. The client's capture state remains
unknown.

## 6. The bridge (mios-inputd)

The Rust daemon is `tools/native/mios-inputd`, installed as `/usr/libexec/mios/mios-inputd`
and built by the native build workflow (static-linkage policy).

The user service runs `serve`. In bridge mode the daemon owns each physical gamepad
evdev node (`exclusive_grab = true`) and forwards captured input through one `mios-vctl-*`
virtual gamepad. The virtual pointer and the virtual keyboard [step I2] are separate
devices. The daemon:
- excludes its own outputs from discovery;
- rejects a duplicate bridge;
- releases held keys and timers on any change of context, profile or owner, on a Guide
  press, on disconnect and on a dropped evdev frame;
- resynchronizes after drops.

Each physical controller has its own chord state, so two controllers can never form one
chord. Force feedback and hidraw forwarding are not implemented.

On a bridge error, `serve` today executes `recovery_argv` (the legacy daemon). After step I1:
- `serve` exits 0 when disabled.
- It idles when the user has no seat or device access (SSH-only logins).
- On an error it exits non-zero, and systemd restarts it under a start limit.

The kernel releases grabs when a descriptor closes, including after the process is
killed. See the kernel's [event protocol](https://docs.kernel.org/input/event-codes.html)
and [uinput interface](https://docs.kernel.org/input/uinput.html).

## 7. Runtime inspection and verification

```sh
/usr/libexec/mios/mios-inputd check
/usr/libexec/mios/mios-inputd map
/usr/libexec/mios/mios-inputd normalize 'SUPER_CTRL+Space'
/usr/libexec/mios/mios-inputd observe --seconds 10
/usr/bin/mios-gate keybind-collisions --root /
```

- `map` emits only the input configuration, never unrelated TOML credentials.
- `replay` takes bounded JSON events on stdin and reports the routed app and pointer
  input, the named actions and the 68-byte Hypr3D state. It does not execute commands.
- Invalid configuration replacements keep the last validated policy.

Run the real executable tests with explicit candidate paths:

```sh
MIOS_GATE=/path/to/mios-gate python3 /tests/test-keybind-collisions.py
MIOS_INPUTD_BIN=/path/to/mios-inputd \
  MIOS_INPUTD_SSOT=/usr/share/mios/mios.toml \
  MIOS_INPUTD_UINPUT_TEST=1 python3 /tests/test-mios-inputd.py
```

If the gate binary is missing, the collision tests fail instead of skipping.
- **Positive controls** check the actual vendor TOML and the Lua projection.
- **Negative controls** plant collisions, terminal steals, VM leaks, invalid bypasses,
  missing or duplicate Lua bindings, and a missing SSOT.

The gate reports dynamic Lua bindings separately. It does not audit live
shortcut-inhibition behavior. The controller completeness audit, in which every input ×
MiOS-owned context maps to an action or `"none"`, is [step GT].
