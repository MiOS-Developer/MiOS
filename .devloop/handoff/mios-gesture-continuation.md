<!-- AI-hint: Uncompiled gesture continuation source, exact recovery provenance, ownership and verification requirements. -->
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
