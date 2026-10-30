<!-- AI-hint: Operator guide to the ShaderGlass overlay pane (miosd shaderglass): keys, per-application profile and window-state persistence across 2D/Hypr3D room/Theatre/PiP/nested sessions, the WineCap command channel, crash relaunch and supervisor resume, the exact-geometry follower, and how to verify it. -->
<!-- AI-related: src/mios-rs/miosd/src/shaderglass.rs, src/mios-rs/miosd/src/shaderglass/state.rs, src/mios-rs/miosd/src/shaderglass/follow.rs, usr/share/mios/shaderglass/patches/winecap-mios-portal.patch, usr/bin/mios-shaderglass, usr/share/mios/mios.toml [desktop.shaderglass], tests/test-shaderglass-state.py -->

# ShaderGlass overlay

MiOS runs [ShaderGlass](https://github.com/mausimus/ShaderGlass) (Windows build,
under Wine + DXVK) as a *pane*: a borderless, click-through window that captures
one target window through the patched WineCap PipeWire bridge and sits exactly
over it. `miosd shaderglass serve` is the supervisor; `mios-shaderglass` is the
key/drawer glue. Everything is configured in `[desktop.shaderglass]` of the SSOT
(`usr/share/mios/mios.toml`, configurator section "ShaderGlass overlay").

## Keys

| Key | 2D desktop | Hypr3D room |
|---|---|---|
| Super+G | pane on the active window on/off | room CRT on/off |
| Super+Shift+G | next shader in the pane (saved for that app) | room CRT cycle |
| Super+Alt+G | setup: ShaderGlass' menu, focusable pane / back to overlay (saved on exit) | room CRT picker |

Controller LB/RB call the same `cycle` / `toggle`.

## What is kept, and when

ShaderGlass keeps its live state (shader, parameters, pixel size, output scale,
aspect ratio, frame skip, flips) only in memory. miosd keeps it **per target
window class** under `$XDG_STATE_HOME/mios/shaderglass/`
(`state.json` + `profiles/<class>.sgp`, 0700/0600):

* **Saved** when a pane leaves setup mode, after every `cycle`, on
  `mios-shaderglass save`, and when a pane closes (detach, Super+G, the target
  closing, `stop`).
* **Restored** whenever a pane starts for a window of that class: the profile is
  ShaderGlass' last command-line argument, which it loads after its own default.

The save uses ShaderGlass' own *Processing > Set as default* through the WineCap
command channel, with the operator's real default profile
(`%LOCALAPPDATA%\ShaderGlassDefault.sgp`) backed up and put back around it, under a
lock. A save interrupted by a crash is undone on the next supervisor start, so
your own default is never replaced. The capture-source keys
(`state.strip_keys`: CaptureWindow, CaptureDesktop, CaptureDevice, CaptureFormat,
InputArea, CroppedArea) are never kept, because miosd always chooses the target.

Anything you pick inside ShaderGlass during setup is kept on exit: a shader from
the menu or the browser, ShaderGlass' own keys (`n`, `p`, ...), parameters,
*File > Load profile*, or a pixel size. *Set as default* in that menu still sets
your own default for new apps.

### The setup menu

Setup mode shows ShaderGlass' menu bar and overlay mode hides it, through the
WineCap command channel (`menu-on` / `menu-off`): WineCap reads the window's
real menu state and toggles only when it differs, then answers with the state
ShaderGlass ended in (`status` shows it as `menu`). The menu and setup mode can
therefore not drift apart, which a relative key toggle allowed (a key that did
not reach Wine left the menu shown in overlay mode, or hidden in setup).

### Across modes and sessions

| Change | Pane process | Shader |
|---|---|---|
| maximize, fullscreen, PiP (small + pinned), another workspace | same | same |
| Hypr3D room / Theatre (`hide_in_hypr3d`) | same, hidden while the room shows | same |
| detach / re-attach, Super+G off/on | new | class profile |
| ShaderGlass crashes, OOM, killed Wine | relaunched up to `state.restart_limit` | class profile |
| supervisor crashed | the unit restarts it (`Restart=on-failure`, at most 3 starts in 120 s, only while `state.resume`) and it re-attaches | class profile |
| supervisor killed or stopped by a signal (logout, upgrade) | re-attached on next start (`resume`, same native window only) | class profile |
| nested MiOS session | its own supervisor unit (`app-mios-org.mios.ShaderGlass@overlay-<hash>`) | shared class profiles |

An explicit `mios-shaderglass stop` forgets the attached panes, so there is
nothing to resume after it. A clean *File > Exit* in ShaderGlass is your choice
and is not relaunched.

## Commands

```
mios-shaderglass toggle | setup | cycle | save | stop | status
miosd shaderglass resume        # start the supervisor and re-attach what a dead one left
miosd shaderglass status        # JSON: panes (profile, restarts, command_channel, menu, last save, placement_gave_up),
                                # hypr3d_room, and the state store
```

## Geometry

Hyprland emits no geometry events, so the supervisor polls the target and puts
the pane on it. hyprctl reports integer logical geometry, and the pane must
match it exactly: a pane one pixel off (wider, narrower, shifted) is drift and
is re-placed. A target rectangle the pane cannot land on (a fractional output
scale that cannot represent it, a client that snaps its size) is re-sent at most
three times. A new target rectangle starts a new budget, so the follower never
loops.

Earlier builds compared with a 1 px tolerance. A pane that ended one pixel wider
than its target, or a target resized by one pixel, was then treated as
"already there" and never corrected.

## Requirements and limits

* The command channel needs the WineCap built from the current patch
  (`mios-shaderglass install --force` rebuilds it). With an older WineCap,
  `cycle` falls back to ShaderGlass' `n` key and setup to its `m` key (a
  relative toggle), and nothing is saved (`status` shows
  `command_channel: false`).
* A nested session runs its own supervisor unit and keeps its 2D/3D hand-over
  files in its private runtime directory. The Hypr3D plugin itself still writes
  the host-global `/tmp/hypr3d-status.txt`, so a Hypr3D room inside a nested
  session is only safe with a private `/tmp` (the live test runs under
  `bwrap --bind <dir> /tmp`).
* Profiles are per window class, not per window: two windows of one app share
  their shader.
* `state.persist = false` turns saving and restoring off. Every pane then starts
  from the default profile.

## Verifying

```
python3 tests/test-shaderglass-state.py                       # SSOT/source + glue (positive and negative)
MIOS_SHADERGLASS_MIOSD=<candidate> MIOS_SHADERGLASS_OLD_MIOSD=/usr/bin/miosd \
  python3 tests/test-shaderglass-state.py Binary              # real binary, hermetic homes
MIOS_LIVE_NESTED=1 MIOS_SHADERGLASS_MIOSD=<candidate> MIOS_SHADERGLASS_INSTALL_DIR=<private install> \
  MIOS_SHADERGLASS_PRIVATE_TMP=<dir> bwrap --dev-bind / / --bind <dir> /tmp -- \
  python3 tests/test-shaderglass-state.py LiveNested          # real ShaderGlass in an owned nested PiP session
```

Without `MIOS_SHADERGLASS_PRIVATE_TMP` (and the matching private `/tmp`) the live
test skips the Hypr3D room / Theatre steps. The installed miosd, the negative
control, always runs with a private `/tmp` of its own: it writes the primary's
hand-over files whatever compositor it serves.

The live test never touches the primary compositor, your prefix, state or
portal. It runs its own nested session, D-Bus, portal stack and Wine prefix, and
uses the installed miosd + WineCap as its negative control (the choice is lost
there).
