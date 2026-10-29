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
  and pinned, resized to the configured PiP width (height keeps the window's aspect ratio),
  raised to the top, and tagged. Ordinary tiling state is untouched for other windows.
- **Leaving PiP:** unpin, exact size and position restore, original pin/float/fullscreen
  flags re-applied in order, origin tag removed. A window that was tiled goes back to tiling.
- **Magnetic layout.** While PiP panes exist, `mios-pip-gravity` gives them soft flank rails
  (left/right columns stacked top-to-bottom) and HUD magnetism without hiding them, with
  aspect-aware sizing and a springy settle; it keeps managed panes above newly mapped
  floating windows. While Hypr3D is active the daemon idles — PiP panes are freely
  resizable in the room. Configuration comes only from the layered SSOT table
  `[desktop.pip_gravity]`.

## Interaction model

- The dock preview settings show a PiP toggle beside the ShaderGlass controls; the preview
  reflects live state (`status` polling marks the pane).
- `mios-pip-disabled` (opt-out tag) is set on windows leaving PiP so the daemon's
  auto-fit never re-captures them against the operator's intent; entering PiP clears it.
- Phone/remote previews are excluded from the preview-settings PiP control.

## Failure behaviour

A PiP action that cannot verify the window's identity, or a restore without a stored
origin, fails with an explicit error and changes nothing — never a partial mutation.
