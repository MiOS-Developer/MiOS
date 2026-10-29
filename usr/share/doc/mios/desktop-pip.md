<!-- AI-hint: Per-window Picture-in-Picture on the MiOS desktop: any Hyprland window can be PiP-ified from the dock preview settings, reversibly, with the original geometry stored on the window itself; mios-pip-gravity owns the magnetic layout. -->
<!-- AI-related: tools/native/mios-pip-gravity/src/{main.rs,window.rs}, usr/share/mios/quickshell/DockPopup.qml, usr/share/mios/mios.toml [desktop.pip_gravity], usr/share/doc/mios/README.md -->

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
  aspect-aware sizing and a springy settle; it keeps managed panes above newly mapped
  floating windows. Automatic PiP recognition also runs while Hypr3D is active; flat rail
  placement pauses while the room owns spatial placement. Configuration comes only from the layered SSOT table
  `[desktop.pip_gravity]`.

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
not remove PiP. The fullscreen controller preserves pinned PiP geometry across
2D maximize and fullscreen. Theatre framing and unrestricted spatial resizing
in Hypr3D still require renderer work.

## Independent desktop sessions

Launch **MiOS Desktop Session** from the application drawer or run
`mios-gen nested-session`. Its desktop actions also offer a regular window and
side-by-side layout. The default parent window is PiP and does not request
initial focus or an automatic controller-capture tag.

The child has a private runtime, compositor, Quickshell entry and Tmux-OS socket.
Ordinary child applications tile inside the current viewport. A native Hyprland
timer reconciles Wayland output configure sizes with the monitor rule so the
workarea reflows instead of keeping the initial large rectangle. The interval
is configurable through `desktop.nested.resize_poll_ms` and the configurator.
The timer changes rules only after the actual output dimensions change.

Closing the parent window stops the owned child desktop and its private Tmux-OS
server. Physical input nodes and the parent's legacy controller packet are masked
inside the child namespace. Per-session controller assignment, remote multiplayer
and VR endpoint attachment remain separate unfinished capabilities; the session
descriptor reports that physical controllers are not assigned.
