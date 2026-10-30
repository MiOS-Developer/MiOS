<!-- AI-hint: Per-window Picture-in-Picture on the MiOS desktop: any Hyprland window can be PiP-ified from the dock preview settings, reversibly, with the original geometry stored on the window itself; mios-pip-gravity owns the magnetic layout. -->
<!-- AI-related: tools/native/mios-pip-gravity/src/{main.rs,window.rs,gravity.rs,matrix.rs}, tests/test-pip-behaviours-live.py, usr/share/mios/quickshell/DockPopup.qml, usr/share/mios/mios.toml [desktop.pip_gravity], usr/share/doc/mios/README.md -->

# Desktop Picture-in-Picture (any window)

Any Hyprland window — native app, VM client, remote session, game — can be turned into a
Picture-in-Picture pane and back, exactly reversibly. The feature is reached from the dock's
live window preview settings (the same gear that holds the ShaderGlass controls), so PiP and
shader controls sit together per window.

## How it works

- **CLI (the single entry point):** `mios-pip-gravity toggle --window <address> --pid <pid>
  --stable-id <id>` — also `on` / `off`, and `status` for the current pane. The dock preview
  invokes exactly this.
- **State lives on the window, not in a registry.** Entering PiP writes two Hyprland tags on
  the window: `mios-pip-managed` and `mios-pip-origin:<floating>:<pinned>:<fullscreen>:<x>:<y>:<w>:<h>`
  — the complete original state. Leaving PiP restores that state exactly and refuses to guess:
  if the origin tag is missing, the restore aborts rather than inventing geometry.
- **Identity guard.** Every generated Lua action first re-finds the window by address AND
  verifies pid + stable id. A recycled window address can never inherit a PiP operation.
- **Entering PiP:** fullscreen is unset (maximized/fullscreen both), the window is floated
  and pinned, resized to the shared opening-policy flank width (height keeps the window's aspect ratio),
  raised to the top, and tagged. Ordinary tiling state is untouched for other windows.
- **Leaving PiP:** unpin, exact size and position restore, original pin/float/fullscreen
  flags re-applied in order, origin tag removed. A window that was tiled goes back to tiling.
- **Magnetic layout.** While PiP panes exist, `mios-pip-gravity` gives them soft flank rails
  (left/right columns stacked top-to-bottom) and HUD magnetism without hiding them, with
  aspect-aware sizing and a springy settle; it keeps managed panes on top (see *Stay on top*).
  Automatic PiP recognition also runs while Hypr3D is active; flat rail
  placement pauses while the room owns spatial placement. Configuration comes only from the layered SSOT table
  `[desktop.pip_gravity]`. Compositor events (window map/close, focus, pin, fullscreen,
  layer changes) wake the daemon at once, paced to at most one tick per `active_poll_ms`;
  title changes are not wake events (a client may retitle every frame); polling (`idle_poll_ms`) is the fallback.

## Interaction model

- The dock preview settings show a PiP toggle beside the ShaderGlass controls; the preview
  reflects live state (`status` polling marks the pane).
- `mios-pip-disabled` (opt-out tag) is set on windows leaving PiP so the daemon's
  auto-fit never re-captures them against the operator's intent; entering PiP clears it.
- Phone/remote previews are excluded from the preview-settings PiP control.

## Failure behaviour

A PiP action checks identity and the stored origin before dispatch. Failed
identity checks and missing origins abort. Native dispatcher failures report an
error; the dock updates its state from the actual result.

## Opening, resizing, and application recognition

The flank width derives from the ordinary window opening policy. Dock, topbar,
PiP spacing and rail margins refer to `theme.shell.edge_margin_px`. The launch
width is a snap target, not a permanently forced width: manual resizing can shrink
or enlarge the pane within its usable output. Neighbours repack across both rails
before falling back to a squeezed stack. Applications retain their own minimum
sizes and client-rendered content, including any padding they draw internally.

Spotify's native mini-player uses a Chromium class and a changing track title.
Recognition therefore requires a same-process Spotify sibling rather than a song
title match. Discord uses its initial popout title; changing channel titles do
not remove PiP. The shipped `match` rules cover Discord's call, stream and video
popouts for the native client, its Flatpak/PTB/Canary builds and Vesktop
(`initial_title = "^Discord Popout$"`), Spotify's mini-player (Flatpak or RPM
`spotify` sibling), Firefox-family `Picture-in-Picture` and Chromium-family
`Picture in picture` windows. The Discord and Spotify main windows and ordinary
Chromium windows are never captured.

**Size magnets.** Within `size_snap_px` of the minimum width (`min_width_px`,
and `min_width_frac` of the output), the launch (flank) width or the maximum
width, a resized PiP snaps exactly to it; between the magnets any size stays.
A smaller resize springs back to the minimum. The maximum is the largest
aspect-true size inside the HUD work area (and `max_width_frac`). Magnets and
the clamp act on each PiP's *natural* size: the size the operator (or the
client) last chose. The daemon only updates it on a resize that is not its own,
so a size the packer squeezed a PiP to never snaps to a magnet later.

**Packing.** Several PiPs stack top-to-bottom on their rail; an overfull rail
moves PiPs to the other flank before both columns are squeezed at one uniform
scale, always clear of HUD bars. On a small output an overfull squeeze wins over
`min_width_px` (no overlap, never under a HUD). Windows enlarged across both
flanks fall back to one shared sequence. The plan iterates positions to an exact
fixed point and emits the integer geometry the daemon applies, so re-planning a
settled layout reproduces it and nothing moves again on the next tick. The fullscreen controller preserves pinned PiP geometry across
2D maximize and fullscreen. Managed PiP in Hypr3D separates spatial scale from
native raster size: client size hints and `pip_raster_max_px` bound allocations,
while the room can continue enlarging or shrinking the surface. Returning from a
large resize restores the optical scale rather than retaining a low-resolution
raster at the old physical size.

Native PiP maximize enters Theatre presentation. The screen targets
`desktop.hyprland.hypr3d.config.windows.theatre_width` metres at
`theatre_distance` metres from the camera, preserving aspect and fitting inside
the HUD workarea. `theatre_fill` and `theatre_seconds` control fit and transition.
Unmaximize restores the original room pose, native size and pin state. Automatic
hover focus cannot dismiss Theatre while moving the pointer across its screen.

## Stay on top

Hyprland 0.56 draws pinned floating windows after every unpinned window and
hit-tests them first, including over fullscreen windows and special workspaces.
That pinned layer is the native stay-on-top: PiP never needs re-raising above an
ordinary window, so the daemon no longer churns the z-order (or the pointer
focus) when another window opens or is clicked. With `keep_above = true` it only:

- re-pins a floating, non-fullscreen managed PiP that stayed unpinned for
  `settle_ms` (a fullscreen PiP is unpinned natively and re-pinned by
  `binds:allow_pin_fullscreen` on restore, so that transient never counts);
- raises a PiP above another *pinned* non-PiP window that overlaps it.

Leaving PiP is the toggle (`off`), not unpinning.

## Fullscreen, maximize and Theatre matrix

| Situation | Behaviour |
|---|---|
| Another window fullscreen | PiPs stay drawn and interactive above it (pinned layer). The HUDs auto-hide and the reserved area collapses, but PiPs keep planning against the last ordinary bounds, so none moves or resizes. |
| That window leaves fullscreen | The bounds latch holds for `fullscreen_release_ms` while the HUDs return, then fresh bounds apply. No jump in either direction. |
| Another window maximized | Same latch; maximize keeps the HUD work area. |
| A PiP maximized / fullscreen | Gravity never fights it. Maximize stays inside the HUD work area. Its pre-fullscreen slot stays reserved in the plan, so neighbours hold still. Unmaximize restores the exact slot and the pin. |
| Hypr3D room active | Flat placement pauses; animations stop, every PiP keeps its last flat slot. Adoption continues. A maximized PiP is the room's Theatre presentation (see above). |
| Room closed | Flat placement resumes from the kept slots: whatever the room moved or re-rastered (native size) is not taken as an operator resize, so every PiP returns at once to its kept slot and natural size. A PiP still maximized after the room keeps its slot reserved until it is restored. |

## ShaderGlass and mode state

A live ShaderGlass pane and its target carry reciprocal native Hyprland tags.
The room captures that same pane's rendered client into the target's client
rectangle; decorations, picking, controller ownership and the room pose remain
attached to the original target. The pane does not become a separate room slab,
and the selected effect is not translated into an unrelated built-in CRT preset.
Broken reciprocal attachments are rejected and detach refreshes the original
client image.

The supervisor continues following native target geometry while the pane is
hidden in a room. `hypr3d.effect_in_room()` distinguishes room composition from
native 2D fullscreen passthrough, where the actual overlay must become visible.
The same live ShaderGlass process retains shader/profile settings across these
changes; this does not imply that an unsaved application profile survives an
application restart.

The isolated renderer proof covers first/third/front camera views, repeated
2D/3D round trips, native fullscreen handoff, attachment removal/reattachment,
PiP raster limits, Theatre entry/restore and fullscreen return. Full application
profile selection under Wine and a Theatre-to-fullscreen-to-Theatre matrix still
need an explicit live application check. Candidate binaries and activation state
are recorded in the development receipts; installing source does not replace an
already loaded compositor plugin.

## Independent desktop sessions

Launch **MiOS Desktop Session** from the application drawer or run
`mios-gen nested-session`. Each session has a private runtime, compositor,
Quickshell entry and Tmux-OS server. Closing its parent window (or the session's
compositor exiting) stops the session, its private Tmux-OS server and its
registry entry. The session's `/dev/input` is empty unless devices are assigned
(below); the parent's legacy controller packet is isolated as described under
"Private /tmp and the room packet".

### Launch targets

`--target` (alias `--layout`; default `desktop.nested.launch_layout`):

| Target | Parent placement | Session scaling |
| --- | --- | --- |
| `pip` (default) | pinned PiP, no initial focus | fit: the `width` x `height` desktop scaled to the PiP; SUPER+CTRL +/-/0/wheel zoom |
| `window`, `side-by-side` | ordinary window, or the right half of the work area | fit |
| `output` | fullscreen on `--output NAME` (a second local user's display) | native: output pixels 1:1 |
| `remote` | fullscreen on the remote display output | native |
| `hypr3d` | a window of the parent's Hypr3D room | fit, at `width` x `height` logical px |

* **output** needs an output name (`--output`, the seat preset's `output`, or
  `desktop.nested.targets.output.output`). An output that is not attached is
  refused before anything starts.
* **remote** uses the output the existing remote path serves: `--output`, else
  `remote.rdp.virtual_output` (the RDP bridge's `HEADLESS-MIOS-RDP`), then
  `desktop.nested.targets.remote.outputs`, then the first attached output with an
  `output_prefixes` prefix. The native RDP server and wayvnc already stream that
  output, so a remote client sees the session; its input arrives on the parent's
  seat and reaches the focused session window like any other window. An output
  reporting 0x0 is never used. With no remote client attached the launch is
  refused, or waits up to `wait_ms`. The RDP server itself is unchanged.
* Every session descriptor (`session.json`) also carries `serve`: `mode =
  "mirror"`, the session's `runtime`, `hypr_instance`, `wayland_display` and its
  only `output`. That is what a remote server needs to share the session itself
  with no parent output at all (the native RDP server's nested lab mode reads the
  same runtime and instance), for example a remote user who should not appear on
  any local display.
* Inside a nested *parent* (a test lab), a headless output cannot allocate
  buffers on this Wayland backend (`GBM: Failed to allocate a GBM buffer`, 0x0),
  so the remote target is verified there on the parent's second Wayland output;
  on the desktop the RDP bridge's headless output is a real DRM-backed output.
* **hypr3d** sizes the window to the session's logical size, clamped into the work
  area minus the shell edge margin, so the session renders at fit scale 1 (one
  texel per parent pixel) and the room's Retina opening shows it at its flat size.
  A closed room is opened through the same path as SUPER+G
  (`hyprland-3d-toggle`) when `desktop.nested.targets.hypr3d.open_room` is true;
  otherwise the launch is refused. The launch succeeds only after the room's
  `export_layout()` lists the window.

Every target's `scale_mode` can be overridden in
`[desktop.nested.targets.<target>]`.

### Seats, users and the registry

`--seat LABEL` (a-z, 0-9, `-`) names a session: its Tmux-OS socket becomes
`<tmux_socket_name>-<seat>`, the session receives `MIOS_NESTED_SEAT`,
`MIOS_NESTED_USER`, `MIOS_NESTED_TARGET` and `MIOS_NESTED_DEVICES`, and a seat label
is unique among live sessions. `--user NAME` is the user's display name (default:
the seat label). `[desktop.nested.seats.<seat>]` presets hold `user`, `target`,
`output` and `devices`; command-line flags win. Unlabelled sessions may run side by
side, as before.

Each live session writes `$XDG_RUNTIME_DIR/mios/nested-sessions/<seat>.json`
(`pid-<launcher>.json` when unlabelled): seat, user, target, output, compositor
PID, Wayland socket and the assigned devices. Entries from a launcher that no
longer runs are ignored and pruned. `mios-gen nested-session --list-sessions`
prints them.

### Device assignment

`--device SPEC` (repeatable), a seat preset's `devices`, or
`desktop.nested.devices.default` assign input devices; `--no-devices` assigns none.
A spec is `/dev/input/eventN` (or a `by-id`/`by-path` link), `name:<exact evdev
name>` or `virtual-pad` (the evdev node `mios-inputd` names
`virtual_pad_name` for this seat). Each node, and with `include_joystick` its
`/dev/input/jsN`, is bind-mounted at its own path into the session's otherwise
empty `/dev/input`; nothing else is visible there. The policy in
`[desktop.nested.devices]` limits kinds (from evdev capabilities: controller,
keyboard, pointer, other), virtual (uinput) nodes and, with `exclusive`, refuses a
node that another live session holds. `--list-devices` shows each host node's
kind and owner; `--plan` prints the resolved launch without starting it.

Assigned nodes are for applications inside the session (games, emulators, an
in-session bridge) and for `mios-inputd`, which reads the registry. The session's
compositor itself only receives its parent window's Wayland input, so a second
local user's keyboard and mouse need the input bridge to stop routing those nodes
to the parent and forward them into the session. That bridge work belongs to the
input daemon; the launcher binds the nodes and publishes the assignment.

### Private /tmp and the room packet

`desktop.nested.private_tmp` (`seat` by default) gives every labelled seat a private
`/tmp` (`<runtime>/tmp`, removed with the session): a second local user, a remote
user or a room seat never shares the operator's `/tmp`, compositor plugin state
files inside the session are its own, and the parent's legacy Hypr3D controller
packet (`/tmp/mios-hypr3d-pad.bin`) is not visible. The descriptor and registry
entry name that directory (`tmp`) and `pad_state`, the file an input bridge writes
this session's own room packet to. `always` applies it to every session, `never`
to none.

The unlabelled development session keeps the shared `/tmp`. Its plugin state files
(`desktop.nested.private_files`) each get a private copy bound over them, and the
legacy packet is masked with `/dev/null`. mios-inputd replaces that packet by
rename several times a second, and a rename detaches a bind over the old file, so
that mask is best effort; a launch that races such a rename inside bwrap is retried
(bounded). Use a seat or `private_tmp = "always"` where isolation from the
operator's controller matters.

### Sessions inside sessions

A session launched inside another nested session lives under the outermost
runtime directory (with a link at the usual place in its parent) because UNIX
socket paths are limited to 107 bytes: `<runtime>/hypr/<signature>/.socket2.sock`
of a runtime nested twice would not fit. Tools that look a session up in its
parent's runtime (session zoom) follow the link.
