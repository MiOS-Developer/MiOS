<!-- AI-hint: Finite HUD source recovery, tall-tile budget repair and pending native verification. -->
<!-- AI-related: tools/native/mios-tamagotchi/src/hud.rs, handoff.md, tasks.jsonl task_3778/task_3779 -->
# HUD continuation milestone

Lane: `mios-hud-continuation`. Base: main
`69fa06efe04f6422bbaec8b4ea0b953c24df2dd5`. Owned paths are `hud.rs` and this
receipt. No UI consumer, input, camera, renderer, task-list or generated-source
changes are included.

The original finite HUD commit
`26eb01d492382da7a59d7b17cf5e24af6be55f27` was cherry-picked as
`18dcb80062b44148e6eea3ddfff00843cd825d68` and pushed to this lane. Its exact
remote head was verified with `git ls-remote`. The entire historical HUD branch
must not be merged: its ancestry conflicts with the source snapshot already on
main in renderer, UI and unrelated SearXNG files. The finite commit changes only
`tools/native/mios-tamagotchi/src/hud.rs`.

## Repair

The recovered geometry budgets one content row before applying the scene share,
then divides the body height by the requested tile height. A 12x6 viewport with
one footer row and three-row tiles therefore produced a four-row panel with a
one-row body and zero selectable items.

`menu_geometry` now reserves one complete tile before applying the scene share.
When the requested tile cannot fit after the border and footer, it explicitly
falls back to one-row tiles. `MenuGeometry.cell_height` reports the effective
height so the UI consumer can choose its label/painter layout consistently.
The existing public `content_panel` API retains its one-row content policy;
its private helper accepts the required minimum body height.

Three added native regressions cover:

- Three-row tiles and every selected option in an offset 12x6 viewport.
- Requested heights 3, 5 and 65535 falling back to one-row options when two
  footer rows must remain visible.
- Selection reveal, nonoverlapping in-bounds hit boxes, footer isolation and
  visible navigation centers from 12x6 to 320x90, at nonzero viewport origins.

## Verification and negative controls

`git diff --check` passes. Rust compiler, Cargo and rustfmt are absent in this
cloud executor. Native tests, formatting, Clippy, release/static validation and
live visual acceptance are **UNRUN**. No source compilation or certified build is
claimed. Run the repository-pinned toolchain and locked workspace in MiOS-DEV:

```sh
cd tools/native
cargo test --locked -p mios-tamagotchi --target x86_64-unknown-linux-musl -- hud::menu_geometry_tests
cargo fmt --check -p mios-tamagotchi
cargo clippy --locked -p mios-tamagotchi --all-targets --target x86_64-unknown-linux-musl -- -D warnings
```

Required compiled negative controls, still **UNRUN**, in a disposable owned
worktree:

1. Change `content_rows.min(minimum_content_rows)` to
   `content_rows.min(minimum_content_rows.min(1))`. Run
   `tall_tiles_retain_a_complete_selected_option_in_micro_panels`; it must run
   and fail because the body becomes one row. Restore the source and confirm
   the same test passes.
2. Change the compact fallback branch of the `cell_height` assignment from `1`
   to `requested_cell_height`. Run
   `oversized_tiles_fall_back_to_single_rows_without_losing_controls`; it must
   run and fail its effective-height or selected-visibility assertion. Restore
   the source and confirm the same test passes.

A compilation error, zero selected tests or skipped test does not establish a
negative-control result. Preserve stdout/stderr and actual command exits with
the source and artifact identities when a builder becomes available.

## Integration boundary and next action

The consumer remains task_3778 work. Current `ui.rs` still paints full-screen
single-column menus. The preserved navigation WIP initializes and reads logical
positions but only its tests populate them; it is not a complete consumer and
does not apply cleanly to current main. A subsequent integration must populate
both visible hit rectangles and full-set navigation from the same geometry,
honor `cell_height`, and test actual buffer painting followed by input.

Keep R4/performance, camera clocks, special preview/editor ownership and physical
controller acceptance in their separate lanes. Publish this source checkpoint
to the continuation PR for native compilation and review before claiming the
HUD task complete.

During the first worktree-creation command, an accidental HUD-only cherry-pick
landed on the original `work` branch. The integration owner restored that
checkout to its exact clean `c1e55b758` after preserving another participant's
in-progress operation. The correct lane and published finite commit above use
the main base. No uncommitted contributions were discarded.

## Choice-menu consumer continuation

The next local milestone reconciles the preserved navigation WIP into current
`ui.rs` and `input.rs` instead of replaying the patch's stale final hunk. It
adds production geometry consumption for **Home, Care, Games and Graphics**.
Their scene paints before a content-sized panel; the panel and tile painters,
click targets, at-most-nine numbered shortcuts and full logical navigation all
come from one `MenuGeometry`. Description and static control rows retain their
own footer space. This first consumer uses compact one-row tiles.

The actual painter publishes the geometry cache. Arrow/D-pad and Alt+4/6/8/2
navigation use its complete item positions, including off-page choices. Tab,
BackTab and touch previous/next retain sequential order. A new menu, Back or
repaint clears the old cache; before first paint only a safe single-column
fallback is available. Numeric labels are a contiguous window within visible
items, keeping the selection numbered even when a wide panel fits more than
nine choices. Mouse/touch can still activate every visible item.

New horizontal aliases and cardinal hints are restricted to the four
choice-menu scopes. The resolver derives their actual labels from the verified
selected profile; missing horizontal aliases retain the truthful vertical-only
hint. Special Roster/Studio camera controls, Seed editing and Status/Help
document navigation retain their existing paths. No camera/animation
implementation was edited. The existing care-description test now locates the
content-sized panel's title where it is actually painted rather than assuming
its title occupies row zero.

Six new native tests render real buffers before input; they do not inject
synthetic navigation positions. They cover cardinal versus sequential focus,
off-page reachability/reveal, resize and menu invalidation, shared numeric/hit
targets, no gameplay charges on navigation/Back, ASCII/mono focus and 12x6
controls, and bounded hit rectangles across four color depths and five sizes.
The existing profile-remapping test also covers cardinal and vertical-only
labels. `git diff --check` passes; compilation, rustfmt, Clippy, native tests,
compiled negative controls and visual review remain **UNRUN** in this executor.

Run the locked MiOS-DEV package suite, particularly
`ui::choice_menu_geometry_tests`, `input::controller_tests`, the prior menu-shell
and `direct_navigation_regressions` modules, and the combined camera tests.
Required compiled negative controls are:

1. Remove the `choice_geometry` publication at the end of `draw_choice_menu`;
   the real-buffer navigation test must fail its painted-cache assertion.
2. Restore linear handling for ordinary directional actions;
   `real_painted_choices_publish_cardinal_navigation_without_charging_gameplay`
   must fail its actual-row/column assertion.
3. Strip off-page positions before publishing the geometry;
   `full_geometry_reaches_off_page_choices_and_reveals_the_new_selection` must
   fail its full-set or reachability assertion.

The operator subsequently required that future publication go **only to main**
for global pickup/handoff. This consumer commit is local; the integration owner
must compile, reconcile it with the camera lane, and publish the verified result
and handoff on main. Do not resume lane-branch pushes from the earlier workflow.
