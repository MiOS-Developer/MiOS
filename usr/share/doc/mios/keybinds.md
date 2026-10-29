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
2D desktop, 3D desktop, terminal, VM passthrough, games, and app capture.
Native fullscreen state and the `mios-input-captured` tag select capture for any
application. VM/game classification also uses the configured native metadata.
Capture takes precedence over room navigation.

`tools/native/mios-resolver/src/keybindings.rs` owns chord normalization for both
the daemon and gate. Keyboard modifiers are ordered `SUPER`, `CTRL`, `ALT`,
`SHIFT`, then side-specific `LEFTCTRL` / `RIGHTCTRL`; keyboard names normalize to lower case. `RIGHTCTRL` never collapses into `CTRL`. Controller names retain their
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

On the desktop, holding Guide enables the configured system layer. Captured
applications own ordinary buttons and Guide combinations while
`captured_system_layer = false`. Guide+Select is the explicit host cursor escape;
the daemon defers Guide until it can resolve that chord. L3+R3 returns the
selected fullscreen application to its active 3D room; an ordinary game outside
that transition keeps both buttons. Each physical controller has independent chord state; two
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

The shipped user service runs **serve mode**, conflicts with the legacy controller
service, and consumes the validated global map. `exclusive_grab = true` enables
one physical evdev owner. The legacy executable remains the configured recovery
path if the bridge fails. `check` reports unresolved handlers explicitly; the
current map resolves all active handlers. The bridge rejects root execution and
concurrent legacy ownership.

Device enumeration runs on the `mios-devices` worker. Rejected nodes are cached
by device and inode identity, and a bounded channel passes validated handles to
the forwarding loop. Closing unrelated evdev devices can wait for a kernel grace
period; these probes and closes must stay off the forwarding thread. Configuration
reloads retain held input when the effective input policy is unchanged. Native
focus observation also runs outside the forwarding loop; stale context falls
back to app capture rather than firing desktop actions.

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


## Native realization and remapping

The configurator at `/usr/share/mios/configurator/mios.html` provides a global binding editor and the controller system-layer map. Existing native actions retain their identity; their chords and contexts can be remapped. New host bindings use the registered action dispatcher. Application-owned entries remain documentation for the application's own input framework.

`action_argv` contains direct executable argument vectors. The runtime consumes the same registry checked by `mios-gate keybind-collisions`. `planned_actions` explicitly prevents dispatch. `native_actions` declares the Looking Glass hold handler realized synchronously by Hyprland; it is not a spawned key-toggle command. Chord release, focus loss, and configuration reload release the original target's held key.

The compositor projection captures its own binding handles. Ordinary host bindings yield in VM and app-capture contexts; declared bypasses use `dont_inhibit` and `submap_universal`. Rule-generated tags include Hyprland's dynamic `*` marker, which is normalized during context matching. VM commands revalidate address, PID, and class in the compositor transaction. Looking Glass uses the configured escape keysym; X11 FreeRDP uses its verified native Right Ctrl escape. Other clients support host fullscreen without guessing their capture protocol. A sent escape is reported as `native_command_sent`; client capture state remains unknown.

`mios-inputd.service` now runs `serve` and conflicts with the legacy service. The bridge keeps the 68-byte native Hypr3D packet and its event sequence counters. On a bridge error, `serve` executes the configured `recovery_argv`. The legacy executable is retained for this recovery path. Private test devices are explicitly excluded from live discovery; udev permission readiness is awaited in the private test harness.

Verification receipts are in `/var/lib/mios-build/mios-inputd/`. The live native service and private kernel fixtures are verified. The physical DualSense is owned by the running bridge. Packet cadence checks and private kernel fixtures provide separate timing and forwarding evidence; they do not certify every application or transport. Native capture requires applications to use the forwarded evdev gamepad path; direct hidraw/force-feedback forwarding is outside this implementation.
