<!-- AI-hint: Manual pages distilled from the source comments of shaderglass, sanitized, each passage anchored to the comment it came from. -->

# shaderglass

### How often one target rectangle is re-sent to a pane that...

How often one target rectangle is re-sent to a pane that does not land on it (a fractional output scale
that cannot represent it, a client that snaps its size) before the follower accepts where it is. A new
target rectangle gets a new budget, and so does a pane that moves away from where it kept landing (the
operator or Wine pushed it off): a bounded number of dispatches per change, never a loop, and a large drift
after a give-up is still corrected.

<!-- mios-src:7815badcdaa3 from src/mios-rs/miosd/src/shaderglass/follow.rs:22-26 -->

### Re-place when the target moved, or when the pane drifted...

Re-place when the target moved, or when the pane drifted from where it was put (Wine or the
operator moved it) and the last placement had time to land -- at most PLACE_RETRIES times
for one target rectangle the pane keeps missing.

<!-- mios-src:c292fea7fd5f from src/mios-rs/miosd/src/shaderglass/follow.rs:318-320 -->

### Setup mode on

Setup mode on: drop the passthrough tag, focus the pane and send ShaderGlass' menu toggle. Off: send the
toggle again (menu hidden) and restore the tag. Focus and shortcut are best effort (pcall). `shortcut`
None: the menu is toggled inside ShaderGlass through the WineCap command channel instead (the same
IDM_TOGGLEMENU the key triggers, without depending on the key reaching Wine), so no key is sent.

<!-- mios-src:d0a55e09571c from src/mios-rs/miosd/src/shaderglass/follow.rs:410-413 -->

### ! ShaderGlass keeps its live state (shader, parameters...

! ShaderGlass keeps its live state (shader, parameters, pixel size, output scale, frame skip, flips, ...) only
! in memory; the one profile it writes without a file dialog is `%LOCALAPPDATA%\ShaderGlassDefault.sgp`
! ("Processing > Set as default"). The patched WineCap runs inside the ShaderGlass process and executes menu
! commands on request (a command file it polls; [`Command`]), so a save is: back the operator's default up,
! remove it, ask the pane for "Set as default", take the file it wrote as the class profile (minus the
! capture-source keys: miosd chooses the target), restore the operator's default. The backup makes the swap
! crash safe ([`recover_default`] puts it back on the next start). A pane launches with its class profile as
! ShaderGlass' last argument (a profile it loads after the default), so a re-attach, a crash relaunch, a
! nested or primary session and every presentation mode the target goes through see the same shader.

<!-- mios-src:39256c27c634 from src/mios-rs/miosd/src/shaderglass/state.rs:5-13 -->

### A shared JSON document (state.json, restore-tokens.json)...

A shared JSON document (state.json, restore-tokens.json) changed by reload-modify-write: an exclusive flock on
`<file>.lock`, the document read from disk, `change` applied, and the result renamed into place -- so two
supervisors (the primary and a nested session share $XDG_STATE_HOME) never write back a stale copy over each
other's changes. `change` returns whether it changed anything; nothing is written otherwise.

<!-- mios-src:9e7b80138236 from src/mios-rs/miosd/src/shaderglass/state.rs:178-181 -->

### $XDG_STATE_HOME/<directory>/state.json, 0600 in a 0700...

$XDG_STATE_HOME/<directory>/state.json, 0600 in a 0700 directory. Shared by every session of the user:
the class profiles are common, the attached-pane record is per session ([`PRIMARY_SESSION`] or a nested
session's key). Nothing is cached: every read is the file on disk, every write a reload-modify-write
([`update_json`]), so one session never undoes another's save or clears another's record.

<!-- mios-src:465cbb0d358b from src/mios-rs/miosd/src/shaderglass/state.rs:230-233 -->

### The command file WineCap polls inside one ShaderGlass...

The command file WineCap polls inside one ShaderGlass process (MIOS_WINECAP_COMMAND_FILE): the supervisor
writes "<seq> <verb>\n" atomically; WineCap claims it, sends the menu command to the ShaderGlass main
window (SendMessageTimeout: done when ShaderGlass handled it) and writes "<seq> ok menu=<0|1>" (whether
ShaderGlass' menu bar is shown afterwards) or "<seq> error ..." to `<file>.ack`. WineCap creates `<file>.ready` when its channel runs: an older WineCap without it is
detected at once, never waited for.

<!-- mios-src:557a81161cf5 from src/mios-rs/miosd/src/shaderglass/state.rs:355-359 -->

### An flock on `<dir>/<name>`: exclusive (`save.lock` around a...

An flock on `<dir>/<name>`: exclusive (`save.lock` around a default-profile swap, `<file>.lock` around a
reload-modify-write) or shared (a pane launch reading the operator's default while no swap runs). A
primary and a nested supervisor share the prefix and $XDG_STATE_HOME.

<!-- mios-src:f04a5137ba19 from src/mios-rs/miosd/src/shaderglass/state.rs:459-461 -->

### Save the pane's live state as `class`'s profile through...

Save the pane's live state as `class`'s profile through ShaderGlass' "Set as default" (`run_save` asks the
pane and returns once ShaderGlass wrote `default_path`), leaving the operator's own default as it was.
Returns the sanitized profile that was recorded.

<!-- mios-src:4b4d89afb009 from src/mios-rs/miosd/src/shaderglass/state.rs:505-507 -->

### The profile a pane of `class` launches with (ShaderGlass'...

The profile a pane of `class` launches with (ShaderGlass' last argument, loaded after its default): the
class profile when one is saved (and `persist` is on), else a snapshot of the operator's default written to
`snapshot`. The snapshot is taken under a shared `save.lock`, i.e. never while a save in any session has
the default swapped out, and ShaderGlass reads it instead of the live default -- so a save another session
starts while this pane's Wine is still starting cannot leave the pane without the operator's default. When
a save holds the lock past `save_timeout`, the backup it made is the operator's default. None: there is no
operator default (ShaderGlass' built-in one applies).

<!-- mios-src:0bcd6c3d975e from src/mios-rs/miosd/src/shaderglass/state.rs:535-541 -->

### No save runs

No save runs: a backup (or the "absent" mark) is what a killed save left, and it -- not the file
ShaderGlass wrote meanwhile -- is the operator's default ([`recover_default`] puts it back).

<!-- mios-src:cabcb986a8d4 from src/mios-rs/miosd/src/shaderglass/state.rs:550-551 -->

### Another session is mid-save (default swapped out, save.lock...

Another session is mid-save (default swapped out, save.lock held): the snapshot is still the
operator's default, from the backup, not the missing file or the pane state ShaderGlass writes.

<!-- mios-src:f025e22e0778 from src/mios-rs/miosd/src/shaderglass/state.rs:793-794 -->

### DXVK_FILTER_DEVICE_NAME for [desktop.shaderglass.wine].gpu...

DXVK_FILTER_DEVICE_NAME for [desktop.shaderglass.wine].gpu = "compositor": the vendor word of the
compositor's primary DRM device (the first of AQ_DRM_DEVICES, which Hyprland's environment carries), so
DXVK renders where the frames are composited instead of copying every frame across PRIME from another
GPU. Derived at run time from sysfs (nothing about the hardware lives in the SSOT); None leaves DXVK's own
choice (gpu = "any", wined3d, no AQ_DRM_DEVICES, an unknown vendor).

<!-- mios-src:3ccb28675528 from src/mios-rs/miosd/src/shaderglass/wine.rs:58-62 -->
