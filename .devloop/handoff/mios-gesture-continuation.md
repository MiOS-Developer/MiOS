<!-- AI-hint: Current global pickup is main; historical stage receipts follow. -->
## Current main pickup

Resume from main and `mios-continuation-milestones.json`. Combined native results
include442 game unit and13 CLI passes (3 ignored),248 controller passes, fatal
Clippy and format. The keybinding test suite passes31 tests. All29 projection
stages completed twice without second-run changes.

The idle-neutral dwell test passed, failed after a compiled tick-time-zero plant, then passed after exact restoration. See mios-gesture-dwell-control.md inside the archive.

Exact source/provenance and raw records are in `mios-native-verification.tar.gz`.
Live Codespace, physical input, full image and R4 acceptance remain open.

---

<!-- AI-hint: Historical source-stage receipt; current summary above supersedes earlier unrun or branch-pickup notes. -->

<!-- AI-hint: Gesture continuation source, exact recovery provenance, ownership and verification requirements. -->
# MiOS deliberate focus gesture continuation

Base: main `69fa06efe`. Lane: `mios-gesture-continuation`.
Recovered commit: `3f3dac3beab2f2a6bb7efe009753c2a23daa49ee`, applied alone
as `bb4c2acf9`. The complete historical lane is not merged. Main's corrected
app-only profile isolation and its native regressions remain in place.

## Native source milestone

Recovered WIP had undefined event-loop `dt`, a missing `FocusGesture` import,
unsupported integer-to-float conversions, and an invalid gate audit call.
These source defects are corrected. Neutral dwell now samples idle ticks;
event frames contribute no invented elapsed time. A cardinal rise survives
multiple samples until its deadline. Peak return, direction/cone validation,
and cooldown remain explicit. Camera, pointer, non-focus bindings and an
absent optional table retain their existing lanes.

Added native regressions cover idle neutral dwell without controller frames,
multi-sample fast rise with focus only on return, app/profile cancellation and
cooldown across a profile change. Existing gesture failure cases and the
absent-table legacy case remain. Native tests and format/lint have NOT run:
this cloud host lacks a MiOS-DEV builder and native toolchain. No binary,
physical controller behavior or deployment is certified by this milestone.

The exact optional table is
`[keybindings.global.controller.nav.focus_gesture]`:

| Key | Vendor value | Native requirement |
| --- | ---: | --- |
| neutral_pct | 0.15 | finite, 0 < n <= 0.5 |
| neutral_ms | 80 | integer, 1..2000 ms |
| rise_pct | 0.9 | finite, neutral_pct < n <= 1 |
| rise_ms | 120 | integer, 1..2000 ms |
| direction_deg | 20 | integer, 1..45 degrees |
| return_ms | 250 | integer, 1..2000 ms |
| cooldown_ms | 400 | integer, 0..5000 ms |

All seven fields must resolve together when the table exists. There is no
enabled field. Magnitudes use normalized post-deadzone travel, not raw axis
percentages. Missing table means the legacy magnitude-flick policy.

## Resume and remaining work

## Configurator source milestone

All seven native settings now have typed, inheritable controls in the existing
controller section. Empty controls omit overrides without copying vendor
defaults into HTML. The real form serializer checks finite fraction bounds,
whole-number time/angle ranges and an explicitly edited peak greater than
neutral. Native validation remains authoritative after layered resolution.

Verification performed in this cloud worktree:

- `python3 tools/test_configurator_security.py`: 11 tests pass. The added
  test executes the actual JavaScript, checks exact control/table membership,
  parses emitted TOML, and rejects malformed, non-finite, fractional integer,
  out-of-range and inverted-threshold values. Existing controls remain covered.
- Negative control: removing upper-bound validation in a temporary HTML copy
  triggers all seven expected out-of-range failures; original source stays
  unchanged.
- `python3 tools/ci-suites.py --check`: 573 suites registered across three
  tiers, six exemptions; pass.
- `git diff 69fa06efe --check`: pass.

No native compile/tests, generator projection, bootstrap mirror synchronization,
live Inputd replacement or physical controller validation has run here.

## Remote verification handoff

Fetch `origin/mios-gesture-continuation` and inspect this receipt and the Git
log in an isolated worktree. In MiOS-DEV, run locked native Inputd and keybinding-gate tests, fatal
Clippy, formatting, release/static checks and compiled negative controls.
Regenerate derived SSOT artifacts through the supported pipeline; do not
hand-edit globals or metadata to simulate a projection pass.

Keep this continuation draft until native compilation and replay validate
the combined source. Upgrade verified Inputd absolute entrypoints before
deploying new controller policy, preserving the documented release ordering.

## Native replay expectation corrections

The integration owner subsequently compiled the combined main source
`375aefba6` in a prepared source-equivalent MiOS-DEV context. That first Inputd
run reported 247 tests: 232 passed and 15 failed. Three failures traced to
incorrect gesture/camera fixture expectations rather than unexpected focus
leakage; the other failures are being investigated by the integration owner.

This follow-up changes only native tests:

- The camera-repeat contract now completes a real base-profile gesture, asserts
  focus happens only after return, then verifies prolonged and partial travel
  do not repeat it. Verified camera-app repeat counts remain unchanged.
- The non-focus test explicitly declares its window-move action. Vendor
  `nav.desktop.RS_RIGHT` is a focus action and cannot stand in for that fixture.
- Button/menu isolation now asserts the declared South tap/hold behavior
  (Enter on short release), scalar D-pad press/release behavior, and exact
  Guide menu output. None may produce an extra focus action.

`git diff --check` passes. Scoped native reruns are pending the integration
owner's serialized build slot; no new runtime or global-green claim is made.
The operator now requires publication only through main. This worker commits
the correction locally for integration and does not push its branch.
