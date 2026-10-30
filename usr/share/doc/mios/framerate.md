<!-- AI-hint: Desktop frame-rate policy: tiers, VRR cap, extended-vblank display modes (DP-3 90 Hz) and the confirm-or-revert guard; plus how Hypr3D windows open at Retina size. -->
<!-- AI-related: /usr/share/mios/mios.toml [desktop.framerate] [desktop.hyprland.mios-pacing] [desktop.hyprland.hypr3d.spawn], src/mios-rs/miosd/src/framerate.rs, src/mios-rs/miosd/src/framerate/plan.rs, tools/native/mios-resolver/src/framerate.rs, tools/native/mios-resolver/src/edid_timing.rs, tools/native/mios-gen/src/hypr3d_spawn.rs, tests/test-framerate.py, tests/test-hypr3d-retina-spawn.py, tests/test-framerate-retina-nested-live.py -->
# Desktop frame rate and Retina window opening

## Frame-rate tiers

`miosd framerate serve` (the user unit `mios-framerate.service`) picks one tier. It sets the rates of the 2D
compositor, Hypr3D, the glass and the wallpaper. The first match wins:

| Tier | When | Desktop D | Glass G | Wallpaper W |
|---|---|---|---|---|
| unlocked | `miosd framerate set-unlocked on` ("at risk") | the output maximum, no cap | D/2 | D |
| gaming | a GameMode client is registered | 120 | 60 | 60 |
| battery | UPower `OnBattery` | 60 | 20 | 30 |
| performance | power profile `performance` | 90 | 30 | 45 |
| balanced, power-saver | the power profile | 60 | 20 | 30 |

- **G** sets the Hyprglass re-sampling rate and the Hypr3D refraction and reflection rate.
- **W** sets the native wallpaper's steps and presents, and the Hypr3D live dome.
- The numbers are in `[desktop.framerate.tiers]`. The configurator lists them under Displays.
- `miosd framerate status` shows the running tier.

## How D is held on an output

| Output | Mechanism |
|---|---|
| VRR active and `mios-pacing` loaded (the default on DP-3) | The plugin caps commits at D. There is no modeset and the change is instant. |
| No VRR, or the output is listed in `mode_outputs` | The daemon switches the display mode. |
| `HEADLESS-*` (RDP/VNC) | Never capped or re-moded. These outputs keep their own pacing (`[remote.rdp]`). |

When the daemon switches a display mode, it picks the mode like this:

1. An advertised (EDID) mode within `mode_tolerance_hz` (0.5) of D is used as it is.
2. Otherwise, with `modes = "extended-vblank"`, the daemon derives a timing from the panel's own EDID. It takes the
   fastest detailed timing at the output's size and adds blank lines to the vertical front porch until the rate is
   D. The pixel clock, horizontal timing and line rate stay the same, so the panel sees its native timing with longer
   frames. This is also what VRR does on every frame.
3. Otherwise, the nearest advertised mode is used.

DP-3 (LG 24GL600F) advertises 144, 120, 100, 75, 60 and 50 Hz, but no 90 Hz mode. Its range limits are 48-144 Hz.
For 90 Hz the daemon derives this timing:

    modeline 332.18 1920 1944 1976 2056 1080 1756 1761 1795 +hsync +vsync    # 90.009 Hz, 161.566 kHz

`miosd framerate modeline DP-3 --hz 90` prints this timing without changing anything. The command refuses in these
cases:

- the rate is outside the EDID range limits, for example 40 Hz;
- the rate is above the base timing, for example 165 Hz;
- no EDID timing exists at the output's size;
- the input is not an EDID.

With `modes = "advertised"` (the vendor value), 90 Hz becomes 99.93 Hz on the mode path. Binaries built before
extended-vblank refuse the other value, so a host or user tier opts in.

## Every display-mode change can be undone

- A switch is verified within `revert_timeout_ms` (5 s): the output must show the mode with DPMS on. If it does not,
  the daemon reverts the switch and blocklists that mode for this panel.
- A verified mode that this panel has not confirmed before is restored after `mode_confirm_s` (15 s), unless
  `miosd framerate confirm-mode` keeps it. After a missed confirmation, the panel gets no more automatic modesets
  until you run `miosd framerate confirm-mode --reset`.
- A mode pinned in `[desktop.hyprland.monitors.<name>].mode` is never changed.
- Stopping the daemon restores every mode it changed.
- A config reload re-applies the mode the daemon set. This includes a modeline. Only a `WxH@Hz` string or a
  well-formed modeline from the state file is replayed.

### Holding DP-3 at a real 90 Hz

The daemon does this natively once a host or user tier opts in:

    [desktop.framerate]
    modes = "extended-vblank"
    mode_outputs = ["DP-3"]

After a SIGHUP (or the next start) the daemon holds every tier on DP-3 with a display mode instead of the VRR cap.
Each new mode goes through the guards above: verified within 5 s, restored after 15 s unless
`miosd framerate confirm-mode` keeps it. Remove the fragment to go back to the VRR cap.

`miosd framerate modeline DP-3 --hz 90` prints the timing and the `hl.monitor` Lua that applies and restores it,
without changing anything. A one-off trial outside the daemon (arm a 15 s revert timer first, switch, verify, confirm
or let it revert) is a lead/operator integration step and is not shipped in the image.

## Hypr3D windows open at Retina size

With `[desktop.hyprland.hypr3d.spawn] criterion = "retina"`, a window that enters the 3D room opens at magnification
M = 1. One window texel covers one output pixel at the window's centre, and text appears at the flat desktop's size.

`mios-gen` computes the spawn distance and scale per output from these inputs:

- the mode, scale and transform (landscape, portrait, flipped);
- the reserved bars;
- the mobile flag;
- the text size (`text_size_source`).

Windows larger than the work area are handled in two steps:

1. They are resized to fit (`fit = "resize"`, `fit_margin_px`).
2. A client that refuses the smaller size is pushed back instead, down to the `min_text_px` legibility floor. Nothing
   is cropped.

The configurator shows the criterion, reading distance, fit, legibility floor, cascade and pixel snap.
`mios-gen hyprland-3d-spawn` prints the plan for each output. `tests/test-hypr3d-retina-spawn.py` measures M on
screen in a nested room, and `MIOS_H3R_NEST_OUTPUT` selects that room's output.

## Verifying in a nested session

`tests/test-framerate-retina-nested-live.py` (opt-in, `MIOS_LIVE_NESTED=1`) runs every check in an owned nested
PiP session (`mios-gen nested-session --layout pip`). The daemon there uses private buses with fake power-profile,
UPower and GameMode services, and a private SSOT tier. The test checks the following:

- each tier's cap, presented rate, glass rate and wallpaper rate;
- the Hypr3D room's rates;
- the extended-vblank plan and its revert path;
- the Retina numbers of the plugin and of `mios-gen hyprland-3d-toggle`, compared with an independent oracle.

A nested session has these limits:

- **The tiers are scaled.** The nested window gets frame callbacks at the primary's own cap, so the test uses tiers
  of 18, 24, 30 and 36 Hz instead of the real ones.
- **A modeline cannot be verified.** Hyprland takes a modeline only on a DRM output, so a nested output cannot show
  one. The test covers the planned modeline and the revert-and-blocklist path. The verified path, where a mode is
  kept only after `confirm-mode`, needs the real panel.
- **The PiP window sets the viewport.** The primary owns the PiP window's size and orientation. A mode, scale or
  transform requested from inside the nested session shows in `hyprctl`, but the output keeps the size the parent
  gave it. Headless outputs do not work in a nested session: they get no swapchain. So rotated and phone outputs
  (1170x2532 at scale 3, mobile) are covered by the oracle and CLI tiers of `tests/test-hypr3d-retina-spawn.py`.
