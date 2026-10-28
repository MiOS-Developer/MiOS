<!-- AI-hint: Global input SSOT, native Rust port, ownership and verification contract. -->
<!-- AI-related: usr/share/mios/mios.toml [keybindings.global], tools/native/mios-inputd, tools/native/mios-resolver/src/keybindings.rs, src/mios-rs/mios-gate/src/keybinds.rs -->
# MiOS global input bindings

`/usr/share/mios/mios.toml` declares the vendor map under
`[keybindings.global]`. The native resolver applies vendor fragments, host
`/etc/mios/mios.toml` and fragments, then the user's
`~/.config/mios/mios.toml` and fragments. An empty user file keeps vendor defaults.
Desktop keyboard bindings remain native Hyprland bindings; the input daemon does
not grab keyboards or replace terminal line discipline.

The Rust implementation is `tools/native/mios-inputd`. Its installed executable
is `/usr/libexec/mios/mios-inputd`, categorized by
`[build.native.categories.daemons]`. Build/install it with the existing native
build workflow inside the development builder. The executable is checked against
the SSOT Linux static-linkage policy.

## Binding data

Each `[[keybindings.global.bindings]]` entry has a unique `id`, tier 1–4,
`chord`, namespaced `action`, optional `contexts`, `passthrough`, `bypass`,
`scope`, and `note`. Missing scope means `host`; `scope = "app"` documents an
application-owned shortcut and never installs a host interception.

The tiers describe desktop/window actions (1), VM and remote-client host keys
(2), terminal actions (3), and controller actions (4). A tier is metadata, not
permission to intercept another application's input. Contexts distinguish the
2D desktop, 3D desktop, terminal, VM passthrough, and games. Anchored class
patterns classify a focused window. VM/game classification takes precedence over
room classification so ordinary input remains available to the focused guest or
game.

`tools/native/mios-resolver/src/keybindings.rs` owns chord normalization for both
the daemon and gate. Keyboard modifiers are ordered `SUPER`, `CTRL`, `ALT`,
`SHIFT`; keyboard names normalize to lower case. Controller names retain their
`BTN_*` spelling and are sorted. Button aliases normalize to the same physical
button. Multi-step sequences use `chord_sep`. The required shared test vectors
are `/usr/share/mios/keybinds/chord-vectors.json`.

The current configured desktop shortcuts include Super+F11 for fullscreen,
Super+Shift+F11 for maximize, Super+F12 for the real MiOS 3D toggle verb, and
Super+Escape for the keymap. Read the SSOT and generated
`/usr/share/mios/hyprland/hyprland.lua` for the effective map rather than using an
older shortcut table. A declared bypass or planned VM action is not proof that
its compositor interception or action handler has been installed.

## Controller ownership and modes

`[keybindings.global.controller]` declares the guide button, system layer,
cursor-mode button and contexts, scan/debounce timing, pointer parameters, and
exclusive-grab switch. The existing
`[desktop.hyprland.hypr3d.controller]` map remains the compatibility source for
camera, movement, grab/resize, ShaderGlass, and face-button actions.

Outside VM/game contexts, unmodified LB cycles ShaderGlass and RB toggles it.
SELECT cycles the 3D camera view. START toggles cursor/camera mode in its declared
contexts; it is never an exit/session-kill action. The 2D cursor path uses a
native uinput pointer, so a graphical terminal/TUI receives ordinary mouse
input when that application supports mouse reporting. Console mouse interaction
also requires the console's existing mouse consumer (such as GPM); a pointer
cannot invent unsupported application menus.

While guide is held, the configured system-layer action consumes the button
instead of replaying it into an app. Without guide, ordinary VM/game inputs
pass through. The mode-override chord permits deliberate cursor-mode entry from
those contexts. Each physical controller has independent chord state; two
controllers cannot accidentally form one chord.

In bridge mode the daemon owns the physical evdev descriptor and forwards
ordinary input through one `mios-vctl-*` virtual gamepad. An app may grab that
virtual pad without starving the physical input owner. The daemon excludes its
own outputs from discovery, rejects a duplicate bridge, neutralizes held keys on
context changes/disconnects, and resynchronizes dropped evdev frames. The virtual
pointer is separate from the virtual gamepad. Force-feedback output and hidraw
protocol forwarding are not implemented by this bridge.

The kernel releases grabs when a descriptor closes, including after process
termination. Private fixtures verify SIGTERM and SIGKILL release, ordinary
forwarding while an app captures the virtual pad, and replay/duplicate rejection.
See the kernel's [event protocol](https://docs.kernel.org/input/event-codes.html)
and [uinput interface](https://docs.kernel.org/input/uinput.html).

## Runtime inspection and verification

```sh
/usr/libexec/mios/mios-inputd check
/usr/libexec/mios/mios-inputd map
/usr/libexec/mios/mios-inputd normalize 'SUPER_CTRL+Space'
/usr/libexec/mios/mios-inputd observe --seconds 10
```

`map` emits only the input configuration, not unrelated TOML credentials.
`replay` accepts bounded JSON events on stdin and reports routed app/pointer
input, named actions and the native 68-byte Hypr3D state without executing
commands. The daemon checks layered configuration changes while running;
invalid replacements retain the last validated policy.

The shipped user service starts in **observe mode**. It does not execute actions,
grab controllers, create application-facing virtual pads, or stop the legacy
controller daemon. `exclusive_grab = false` remains the vendor default. Physical
migration is separate: prove private fixtures and compatibility, resolve every
active action handler, stop the previous sole owner, and explicitly enable the
bridge. The bridge rejects root execution and concurrent legacy ownership.
Planned VM passthrough and shell actions must not be described as working until
their handlers and compositor projection have been verified.

Run the real executable tests with explicit candidate paths:

```sh
MIOS_GATE=/path/to/mios-gate python3 /tests/test-keybind-collisions.py
MIOS_INPUTD_BIN=/path/to/mios-inputd \
  MIOS_INPUTD_SSOT=/usr/share/mios/mios.toml \
  MIOS_INPUTD_UINPUT_TEST=1 python3 /tests/test-mios-inputd.py
```

The collision tests require the real gate binary; an absent binary fails rather
than skipping verification. Positive controls check the actual vendor TOML and
Lua projection, including literal string concatenation. Negative controls plant
collisions, terminal steals, VM leaks, invalid bypasses, missing or duplicate
Lua bindings, missing projection files, and missing SSOT. The gate reports
dynamic Lua bindings separately; it does not pretend to audit unresolved
expressions or live shortcut-inhibition behavior.

POSIX signals, Readline editing and the configured tmux prefix remain owned by
the terminal. Ctrl+B is not a universal background command, and Ctrl+S may be
terminal flow control. Do not turn either into a global host interception.
