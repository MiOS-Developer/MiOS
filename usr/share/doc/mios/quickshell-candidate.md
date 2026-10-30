<!-- AI-hint: Staged Quickshell 0.3.2 candidate that fixes the shell's Qt update-group freeze and Hyprland event-socket deafness: how it is built against the host Qt, how it was validated in an owned nested session, and the lead's install / rollback plan for the live desktop. -->
<!-- AI-related: /usr/share/doc/mios/README.md, /automation/66-bake-quickshell.sh, /src/quickshell/check-update-groups.py, /src/quickshell/patches/hyprland-event-socket-reconnect.patch, /src/quickshell/install-candidate.sh, /tests/test-quickshell-candidate-install.py, /tests/test-quickshell-candidate-live.py, /tests/test-shell-hud-live.py -->

# Quickshell candidate (status island / dock freeze)

## The fault

The installed Fedora package `quickshell-0.2.1^git20260209.dacfa9d` returns from
`HyprlandToplevel::updateFromObject()` while a Qt property update group is still
open. After enough toplevel churn every computed QML binding in the shell stops
updating. The status island and dock then stop reserving their rows (tiled and
maximized windows run under them, `reserved=[0,0,0,0]`), and a fullscreen window
no longer hides the HUD. Recreating layer surfaces from QML cannot repair it; the
fix has to be in the binary.

A second fault appears under an event storm. Hyprland closes the event socket of a
client that falls behind (`QLocalSocket::PeerClosedError`). Upstream Quickshell
never reconnects, so the shell stops seeing fullscreen, workspace and window
events, even after the update-group fix.

## The candidate

* Upstream tag `v0.3.2` (`4f508be500dea6e5732cc3d50382a0048b17e7b1`), the newest
  stable release, which already closes every update group
  (`src/quickshell/check-update-groups.py` passes it without the older-ref patch).
* Plus the carried `src/quickshell/patches/hyprland-event-socket-reconnect.patch`
  (reconnects with backoff, then resyncs monitors, workspaces and toplevels).
  `automation/66-bake-quickshell.sh` applies the same patch, so the next image
  build ships the same fix.
* Built **on the host**, against the host Qt 6.11.2 (Fedora 44): the matching
  Qt devel packages exist there, not in the builder container. The build was
  `nice -n 15`, used `ninja -j2`, and was installed with `DESTDIR` into a private
  stage. Nothing was installed into `/usr`.
* Stage: `/home/mios/mios-desktop-work-1/mios-shell-lanes/qs-stage` (the same
  paths as the RPM: `usr/bin/quickshell`, `usr/bin/qs`,
  `usr/lib64/qt6/qml/Quickshell/**`).
  `quickshell --version` →
  `Quickshell 0.3.2 (revision 4f508be5…, distributed by MiOS staged candidate (Fedora 44 Qt 6.11.2))`,
  binary SHA256 `6318b739b6a423c4e2b4dbae15979a7894d07b9161037d4f47a05a0c90a47604`,
  with no unresolved `ldd` entries. The staged binary is an unstripped
  RelWithDebInfo build (about 224 MB); the install script ships it stripped
  (about 5.8 MB, the packaged size). Every directory in the stage is mode 0700,
  so the stage must never be copied as a tree. The same ref built without the reconnect
  patch (`qs-unpatched/quickshell`) is kept as a negative control.

## Validation (owned nested PiP session; the primary shell is never touched)

`tests/test-quickshell-candidate-live.py` (`MIOS_LIVE_NESTED=1`) runs three
binaries in turn with the MiOS nested shell QML. The churn is three rounds of
windows hopping between new named workspaces, more than 40,000 moves per binary.
An event-socket stall is created by stopping the shell while the churn continues.

| Binary | Bindings after churn | Socket dropped → reconnected | Fullscreen hides the HUD | Maximized inside the reserved rows |
|---|---|---|---|---|
| installed 0.2.1 `dacfa9d` | **frozen** (negative control) | yes → no | no | yes (frozen values) |
| 0.3.2 without the reconnect patch | follow | yes → **no** (negative control) | **no** | yes |
| **0.3.2 candidate** | follow | yes → **yes** | **yes** | **yes** |

Regression: the full desktop shell (`Config.qml`) loads under both the installed
binary and the candidate. The candidate prints no QML diagnostic that the
installed binary does not also print, and the nested shell QML loads without
errors. Latest run: 23/23 passed.

## Install plan (lead only; not run by the shell lane)

`src/quickshell/install-candidate.sh` (in this tree) installs the stage file by
file. It never extracts an archive over the root and never creates, chmods or
chowns a directory that already exists. The earlier
`tar -C "$STAGE" -cpf - usr | tar -C / -xpf -` plan would have reset `/usr`,
`/usr/bin` and `/usr/lib64` to the stage's mode 0700 and locked every user out
of the system; `tests/test-quickshell-candidate-install.py` reproduces that as
its negative control.

1. Dry run, as yourself (changes nothing; lists every `replace` / `create`):
   `src/quickshell/install-candidate.sh plan --root / --stage /home/mios/mios-desktop-work-1/mios-shell-lanes/qs-stage`
2. Install, as root:
   `sudo src/quickshell/install-candidate.sh install --root / --stage /home/mios/mios-desktop-work-1/mios-shell-lanes/qs-stage`
   * The stage is validated first. Only the candidate's own paths are allowed
     (`usr/bin/quickshell`, `usr/bin/qs`, `usr/lib64/qt6/qml/Quickshell/**`,
     the desktop entry and the icon); symlinks must stay inside that set;
     special files, `..`, tab or newline names and symlinked ancestor
     directories are refused. The staged `quickshell --version` must report
     0.3.2. It runs as the invoking user (`SUDO_UID`), never as root.
   * Every destination that exists is backed up with its mode, owner, label
     and link target into
     `/var/lib/mios-build/rollback/quickshell-candidate/txn-<UTC>-<pid>/`
     (0700) and verified by SHA256 before anything is written. A failed backup
     stops the install.
   * Every file is written under a temp name in its own destination directory
     with an explicit mode (0755 under `usr/bin`, 0644 elsewhere, root:root),
     the binary through `strip --strip-unneeded` (debug sections are
     asserted gone and the stripped binary must still run), and then renamed
     into place. A missing directory is created 0755 and journalled.
   * SELinux labels are restored on exactly the installed paths.
   * Asserts: no pre-existing directory changed mode or owner
     (`stat -c %a /usr /usr/bin /usr/lib64` is printed: 755 755 555), and every
     installed file matches what was prepared.
   * Any failure or interrupt undoes the partial install: replaced files come
     back from the backup, created files and directories are removed, and the
     journal is kept as `txn-….failed`.
   * A second install is refused while one is active
     (`install-candidate.sh status --root /`).
   * It starts, stops and signals nothing. The running shell keeps the binary
     it has mapped, because each file is replaced by rename, not rewritten.
   * `rpm -V quickshell` reports the replaced files as modified until rollback
     or the next image update. That is expected.
3. Before you load the new binary, check that the **loaded** unit has
   `KillMode=process`:
   `systemctl --user show mios-shell -p KillMode`. A restart under
   `control-group` kills every app launched from the shell's cgroup.
4. Restart the shell only when the operator agrees. Then check:
   `quickshell --version` reports 0.3.2; `hyprctl -j monitors` shows the status
   island and dock reserved rows; and a fullscreen window shows only the hot
   zones.
5. Persistence: the deployed `/usr` copy lasts until the next image update. The
   next image build ships the same fix through bake 66 (latest stable +
   reconnect patch).

## Rollback

* `sudo src/quickshell/install-candidate.sh rollback --root /` checks every
  backup against its recorded SHA256, restores each replaced file by
  rename (mode, owner, label and the original absolute `qs` link included),
  removes the files and the directories that only the candidate shipped (the
  `Wayland/_BackgroundEffect` and `WindowManager` modules), verifies the result
  and keeps the backup as `txn-….rolled-back`. The test checks that the tree
  is byte-identical to the pre-install tree afterwards.
* `dnf reinstall quickshell` is the fallback. It restores the package files
  but not the candidate-only QML modules; remove those by hand in that case.
* Then restart the shell under the same `KillMode=process` rule.

## Known limits

* Validated in a nested session only. The primary desktop has not run the
  candidate.
* Rapid toplevel churn and socket stalls are synthetic. Real long-running sessions
  should be watched with the `EdgeReveal.qml` binding probe after deployment.
