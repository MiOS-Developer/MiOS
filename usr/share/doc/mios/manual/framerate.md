<!-- AI-hint: Manual pages distilled from the source comments of framerate, sanitized, each passage anchored to the comment it came from. -->

# framerate

### panel identities whose last automatic modeset the operator...

panel identities whose last automatic modeset the operator did not confirm in time: no more automatic modesets
there (the output goes back to its baseline) until `miosd framerate confirm-mode --reset`

<!-- mios-src:ada214530500 from src/mios-rs/miosd/src/framerate/plan.rs:177-178 -->

### The plan for these inputs on these outputs. D and the cap...

The plan for these inputs on these outputs. D and the cap come from the policy (D capped at the largest local
refresh); per output: excluded -> left alone; VRR active (or require_vrr off) with the plugin loaded -> the cap;
else, unless mechanism = "cap" or the operator pinned the mode, the mode for D (`choose_mode`). An output in
`mode_outputs` takes the mode path even with VRR and the plugin available.

<!-- mios-src:df16f0512e05 from src/mios-rs/miosd/src/framerate/plan.rs:226-229 -->

### mode_outputs forces the mode path, except on an output...

mode_outputs forces the mode path, except on an output whose mode the operator pinned: that one keeps the
policy's own mechanism (the VRR cap where it can), since the daemon never changes a pinned mode

<!-- mios-src:5c7be6babbbe from src/mios-rs/miosd/src/framerate/plan.rs:244-245 -->

### How the daemon names the mode `m` shows, for its baseline...

How the daemon names the mode `m` shows, for its baseline and every revert snapshot. A "WxH@Hz" string of a mode
the output does NOT advertise is never used: Hyprland would program it as a custom CVT timing (no reduced
blanking, not in the EDID). In order:
1. `set`: the exact mode string (an advertised mode or its own modeline) the daemon last put on this output, when
   the output still shows it;
2. the advertised mode the output shows (refresh within 0.01 Hz);
3. an output that advertises nothing (a virtual or nested output: its modes are custom anyway): the shown mode;
4. otherwise (someone else's custom mode or modeline, e.g. the DP-3 apply script's): the configured rule's mode
   (`rule`: [desktop.hyprland.monitors.<output>].mode, else the catch-all's, "preferred" by default) among the
   advertised modes at the current size -- "highrr" the fastest, "preferred" the EDID's first timing, else the
   fastest -- with `exact = false`.

<!-- mios-src:eb96991b1f4d from src/mios-rs/miosd/src/framerate/plan.rs:340-350 -->

### Whether an automatic switch of panel `identity` to `mode`...

Whether an automatic switch of panel `identity` to `mode` must be confirmed by the operator within `confirm_s`
seconds ([desktop.framerate].mode_confirm_s) or be reverted: always, except with confirmation off (0), for the
output's baseline (the mode it showed before the daemon first switched it: going back is the revert itself) and
for a mode the operator already confirmed on this panel.

<!-- mios-src:c2d1ac8e0f1a from src/mios-rs/miosd/src/framerate/plan.rs:410-413 -->

### Test hook (MIOS_FRAMERATE_TEST_MODES =...

Test hook (MIOS_FRAMERATE_TEST_MODES = "NAME=WxH@Hz,WxH@Hz;NAME2=..."): extra advertised modes for VIRTUAL
outputs (HEADLESS-* and WAYLAND-* only -- a headless or a nested Wayland-backend output: their modes are custom
anyway, and such an output lists only the custom mode it was last given, or none), so the nested tests exercise
the modeset, confirmation, revert and baseline paths. A real panel's (EDID) list is never touched.

<!-- mios-src:23ae50c95d5d from src/mios-rs/miosd/src/framerate/plan.rs:444-447 -->
