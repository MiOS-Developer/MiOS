<!-- AI-hint: Operator-requested engineering handoff for live desktop ownership, HUD/session resilience, remote delivery and Controls/Tmux work. Read current state before repeating a deployment. -->
<!-- AI-related: .devloop/LEDGER.md, .devloop_artifacts/manifest.json, tools/native/mios-inputd, usr/share/mios/mios.toml, src/mios-rdp-server, usr/lib/systemd/user/mios-shell.service -->
# Desktop continuation handoff — 2026-10-29

The operator requested a handoff before the outgoing participant reaches quota.
The incoming participant is `mios-desktop-repair-session`; outgoing participant
is `mios-rdp-live-recovery`. These are current participants, not permanent roles.
The broad desktop goal remains unfinished. Continue coding and live verification.

## Native remote camera forwarding — 2026-10-30T15:52:07.134521+00:00

This supersedes the older camera placeholder status. RDPECAM enumeration and per-device channels are implemented in Rust in `remote_camera.rs`. Client-selected color streams negotiate bounded formats; H.264/MJPEG use native decoders and NV12/I420/YUY2 use packed raw frames. Each connection owns PipeWire Video/Source providers. Consumer activity pauses/deactivates and reopens capture. Device removal, enumeration close, malformed samples, worker failure and disconnect close only owned camera resources. Connection-local DVC actions preserve the existing graphics/input/audio channels, and ownership tokens prevent old queued actions from affecting replacement sessions. The static source preparer supplies guarded upstream extension hooks.

Camera permission is enabled in the SSOT and HTML, along with provider/graph executables, dimensions, FPS, device and timing bounds and source naming. Native camera programs target the operator's PipeWire runtime even when the display is nested. User instructions and compatibility limits are in `usr/share/doc/mios/remote-camera.md`. PipeWire-aware consumers and portals are supported; legacy V4L2-only applications need a separate bridge. A brief priming capture publishes the source before consumer-driven idle deactivation. No physical camera frames were recorded by tests.

LIVE server SHA256 `bb6dcd8f3ef84e7b217daf40fb528d0311acb5e676a82041bcd5a75c2d5ddcba`, PID `3242106` at idle activation. Production test-auth is false. RDP listener only was restarted after the operator disconnected; primary compositor5764, application identities and physical geometry were preserved. Package/rollback/receipt use `mios-camera` under `/var/lib/mios-build/rdp-delivery`. The first transient idle watcher failed before execution because systemd could not open its output file (209/STDOUT); no pending watcher is claimed. Activation then ran directly while disconnected and passed all preservation checks.

Verification: 67 regular Rust tests; four static preparation tests; both recorded codec fixtures explicitly run; 37 backend checks with33 passes/four documented skips. `mios-camera-final-native-result.json` has four cases covering three codecs plus enumeration close, real encoded DRDYNVC PDUs,12 changing native frames per case (1382400 decoded bytes), consumer idle/reopen, permission denial, hot unplug before connection drop and source cleanup. This is an in-process protocol loop plus actual PipeWire consumers, not a physical mobile camera or TCP camera interoperability claim. Full TCP/TLS FreeRDP microphone and native filesystem regressions passed in the owned nested PiP lab: `mios-camera-owned-microphone-regression.json` (240000 synthetic samples, RMS7958.77) and `mios-camera-final-files-regression.json` (262144/98304 file bytes and managed-service cleanup). These are not phone FPS measurements.

Host incident: installing pipewire-utils also updated matching PipeWire packages from1.6.9 to1.6.11. The still-running older Pulse daemon crashed while unloading a new pipe-tunnel module during the first microphone regression. The core dump shows old deleted libraries with a new module, suggesting mixed in-process versions; do not claim a proven upstream root cause. Pulse automatically restarted once with new libraries. A narrowly owned temporary recovery service restored the real client's source on its existing FIFO without changing defaults, then cleaned up after the operator disconnected. Fresh microphone enabled/disabled, cleanup and default-route controls passed. Rust cleanup now verifies the module's type and exact owned FIFO before unloading a numeric ID, preventing reused IDs from unloading another source after a daemon restart. Current server creates fresh resources on reconnect. The backend still needs a future automatic audio-daemon-restart recovery path; that is not claimed here.

The operator is reconnecting to test physical phone camera/microphone input. Inspect live device_forwarding.camera sources/errors and confirm actual client negotiation before reporting that physical camera interoperability passed. The primary audio source had nonzero input during a short pre-update live level check; no recording was retained. Source readiness alone is not audio quality verification. Native speaker output, generic USB, non-RDP device bridges and V4L2 compatibility remain unfinished. The nested test status now says FINISHED; its desktop was left running for the operator to close.

## Native remote file forwarding — 2026-10-30T15:17:25.535969+00:00

This supersedes the older RDPDR stub status below. Real RDPDR client drives are now exposed as per-connection native FUSE filesystems under the operator runtime directory. Reads, writes, listings, create, mkdir, rename, truncate, delete and remote volume capacity are implemented. Device, inode, directory and IO limits are bounded; traversal/reparse entries are rejected. A transport timeout poisons that connection until reconnect, preventing late replies from matching a new request. Lookup references and open file handles preserve native unlink/recreate semantics. Client permission remains required.

Live server SHA256 `a19368271b9144ee680d58dfacf98e949a06676a291075e1cf1903f2ca0c0a54`, PID `2581937` at idle activation. Primary compositor5764, application identities and physical monitor geometry were preserved. Production test-auth is false. Package and rollback use `mios-storage` under `/var/lib/mios-build/rdp-delivery`. The native unit uses KillMode=mixed so SIGTERM reaches the server first and its owned FUSE/audio workers can unmount before systemd kills remaining children. No desktop restart occurred.

Verification: 59 regular Rust tests, three static source-preparer tests, both recorded codec fixtures, 33 backend checks and four documented skips. Real FreeRDP redirected 262144 bytes client-to-host and 98304 bytes host-to-client; filesystem mutations, open-unlinked files, volume capacity, disabled permission and attached service-stop cleanup passed. Current microphone regression captured 240000 synthetic samples with RMS7954.2; default routing and cleanup passed. Reports: `mios-storage-service-result.json`, `mios-storage-network-result.json` and `mios-storage-microphone-regression.json` in the evidence directory. These are private nested-session tests, not phone FPS measurements.

The upstream RDPDR server advertises USER_LOGGEDON but omitted the negotiated notification. `prepare.rs` is a static Rust compatibility generator executed before dependency compilation; known source boundaries and positive/negative/idempotence tests guard the fix. A first fixture used a shared upstream symlink; only our change was restored, the cache hash verified, and this build now uses an isolated checkout. See `mios-rdp-storage-upstream-isolation.json`. Package receipt metadata was corrected after discovering a packaging helper loop variable shadowed the candidate name; installed binary hashes were unaffected.

The operator explicitly requested camera forwarding next. Camera, native speaker sink, generic USB and compatible non-RDP device bridges remain unfinished. The owned nested PiP lab stays open with a visible test status; the operator confirmed earlier closures were intentional.

## Remote hardware continuation — 2026-10-30T03:37Z

This section supersedes the RDP defaults and active binary below. The operator
added camera, USB, file and microphone forwarding for RDP and compatible remote
clients. **The full hardware-forwarding request is unfinished.**

* Real MS-RDPEAI microphone forwarding is implemented in `redirect.rs` and live.
  Native 48 kHz signed PCM mono/stereo is validated, stereo is downmixed, and
  bounded queues discard stale packets. Each connection owns a PipeWire-Pulse
  source and private FIFO; close and attached-server shutdown remove them.
  No physical source/default routing is changed by the implementation.
* Live server SHA256 is
  `7ee099f9f5fd1e5fdb148af13d530d6a543b1766b91b4f63813a32b429a65373`,
  PID `612545` at activation. Package/rollback/activation receipts use the
  `mios-microphone-wire` stem under `/var/lib/mios-build/rdp-delivery/`.
  Activation was idle; primary compositor PID5764, application identities and
  physical monitor geometry were preserved. Production test-auth is false.
* SSOT microphone permission is now true, still subject to client permission.
  HTML exposes permission, native audio executable, queue size and source name
  prefix. `pulseaudio-utils` is included in the remote-desktop package set.
  Stats expose native readiness, source, bytes, drops and errors. Other device
  capabilities explicitly report their missing backends.
* Progressive capture now defaults to `progressive_hz=0`, inheriting the
  operator's target (90 Hz on this host), rather than imposing my earlier 30 Hz
  default. This is a ceiling policy, not a measured 90 FPS claim.
* `WireStream` counts successful socket writes below TLS for every codec.
  `bitrate_kbps` includes TLS/RDP framing; `encoded_payload_kbps` retains the
  encoded EGFX payload rate. Bitmap no longer incorrectly reports zero bitrate.
* Verification: 54 regular Rust tests; both recorded codec fixtures explicitly
  executed; 37 backend checks, 33 passing and four documented opt-in/operator
  skips; production hardware/self-test and SSOT/global projection checks.
  Native callback audio captured 24,000 samples with RMS4480.65. Actual FreeRDP
  forwarding captured 240,000 synthetic samples with RMS7949.91. Its disabled
  control created no source, and source/default cleanup passed. No physical mic
  was recorded. The new attached-SIGTERM check failed on the old binary and
  passed on the new one. Reproducible fixture:
  `tests/fixtures/mios-rdp/microphone.py`; run as the desktop operator with
  `MIOS_RDP_NATIVE_LIVE=1`, `MIOS_RDP_BIN` pointing at a private test-auth build,
  `--nested-descriptor <owned session.json> --report <owned report.json>`.
* Wire-codec E2E passed in the owned nested PiP lab: Progressive29.5 ACK FPS,
  bitmap14.5 write-complete FPS (32–34 Mbps actual socket traffic), AVC29.5 ACK
  FPS. Reports are `mios-wire-microphone-codec-e2e.json` and
  `mios-microphone-shutdown-result.json` in the build evidence directory.
  These measurements use a 30 Hz test target and are not phone FPS.
* One owned nested lab remains running: descriptor
  `/var/tmp/mios-rdp-microphone-lab.json`, unit `mios-rdp-microphone-lab`.
  It uses isolated input and no physical controller assignment. Do not close
  operator-adopted nested sessions as test cleanup.

Remaining device work: RDPSND native speaker sink, server-side RDPECAM DVC plus
native video source, RDPDR native filesystem/FUSE operations, URBDRC native USB
bridge, and compatible non-RDP SPICE/usbredir/USB-IP integration. RDPDR now rejects
devices instead of claiming empty directories are mounted drives. The upstream
camera crate has client/PDU code but no server implementation. Generic USB support
also depends on the client: Microsoft's platform matrix does not offer generic
USB forwarding on iOS Windows App. Camera/microphone use separate protocols.
Native worker attempts failed at login or unified endpoint stream startup; no
device worker implementation or registration is claimed. Worker assignment
requests remain queued for `mios-desktop-repair-session`.

Build incident: the shared lock was held for18 minutes by a bidirectional
`git cat-file --batch` pipe deadlock in a generator golden test. Exact process,
FD and signal evidence is `mios-generator-deadlock-receipt.json`. Only the verified
Git child received SIGTERM; its generator exited and the golden suite reported
failure. The generator's write-all-before-output-read pattern in `indexes.rs`
still needs a concurrent writer/drain fix and a fresh golden pass. Do not claim
that cancelled suite passed. The desktop coordinator received the root cause
and failure receipt through the native relay.

Primary references: [PipeWire pipe-source](https://docs.pipewire.org/page_pulse_module_pipe_source.html)
and [Windows App device compatibility](https://learn.microsoft.com/en-us/windows-app/compare-platforms-features).

## Continuation update — 2026-10-30T01:54:34.636288+00:00

This section supersedes the older RDP and integration status below. The
operator requested continued coding after the initial handoff. Root was advanced
by the incoming participant through `90281a2f6`, integrating Controls, nested fit
scaling, session scaling, PiP drawer behavior and the S0 global binding contract.
Other platform/input/shell worktrees remain separate; inspect their receipts
before integrating. No broad backlog completion is claimed.

### RDP Progressive is implemented and live

* Production server SHA256 `362a4f9b45943a5b4122cca8fbc67e119d73669504eafe1407df42484c155b39`, PID
  `2939898` at activation. Installed under
  `/usr/libexec/mios/rdp-native/mios-rdp-server`. Recheck the PID before use.
* The final verified package is `/var/lib/mios-build/rdp-delivery/mios-progressive-final-candidate`.
  Production hardware self-tests explicitly report `test_auth_build=false`;
  the private test-auth build was never installed into the production path.
* The new path selects RemoteFX Progressive when EGFX is ready and AVC420 is
  unavailable. Old V8/V8.1 thin clients retain the legacy fallback. Independent
  damaged 64x64 tiles reference only the last successfully submitted frame;
  edge pixels are replicated and frame acknowledgements bound queued work.
* SSOT and HTML expose `remote.rdp.native.progressive=true`,
  `progressive_hz=30`, `progressive_quant=8` (valid quantization 6..15).
  Set progressive false to preserve the legacy fallback. H.264 remains preferred
  for capable clients. Generic `fps_basis` identifies client EGFX acknowledgements
  versus legacy bitmap socket-write completion.
* The final refinement reuses one 64 KiB entropy scratch buffer instead of
  retaining large scratch capacities in every encoded tile. It also clears a
  previous connection's bitmap pacing/reporting state when EGFX takes ownership.
* Verification: 48 regular Rust tests, both recorded fixtures executed explicitly,
  37 backend checks (33 pass, four documented live/operator skips), OpenH264/EGL
  self-tests and SSOT/global projection checks. Real DVC negotiation, ACK
  backpressure, encode/decode, edge tiles, malformed frames, retained contexts and
  the real encoding thread's stale-connection state are covered.
* A recorded 1100x2080 frame encoded to 58,492 bytes; the first encoding measured
  23.2 ms before scratch reuse. All 594 tiles decoded successfully. Prior measured
  NSCodec updates were 1–2.7 MB. These fixture measurements are not phone FPS.
* Actual nested PiP + FreeRDP delivery: Progressive 29.5 acknowledged FPS,
  explicit Progressive-disabled bitmap control 14.5 write-complete FPS, AVC420
  29.5 acknowledged FPS. The final repeated test is
  `/var/lib/mios-build/rdp-delivery/mios-progressive-final-e2e.json`; screenshots/log paths are in that JSON.
  The test's owned nested output was mirrored. Creating another headless output
  under this Wayland-only nested backend yielded 0x0 and GBM EINVAL; that separate
  nested-platform limitation remains open.
* The actual mobile peer subsequently connected to the refined Progressive build
  `308437a7...`: a single initial sample showed 28.5 ACK FPS. A 30-second, 15-window
  sample showed 16.5–22.5 FPS, median
  18.5, median capture-to-ACK p50 68.3 ms.
  These are remote client frame acknowledgements, not local compositor FPS or
  measured display scanouts. Record variation; do not advertise the peak as a
  sustained rate. Source: `/var/lib/mios-build/rdp-delivery/mios-progressive-phone-samples.json`.
* The final `362a4f9b...` build adds the connection-state reset and was activated
  afterward, only when the phone disconnected. Its next phone connection still
  needs sampling. The activation receipt is
  `/var/lib/mios-build/rdp-delivery/mios-progressive-final-activation.json`; rollback package is
  `/var/lib/mios-build/rdp-delivery/mios-progressive-final-rollback`.
* Each activation guard required the client to be idle, and verified primary
  compositor PID 5764, existing application identities and physical output
  geometry. No desktop restart or reboot occurred. The old idle helper finished;
  do not launch it again against an already-created rollback directory.
* Remaining RDP work: longer real phone motion/latency/input checks, bitmap wire
  bitrate telemetry (legacy still reports zero), and quality/rate adaptation
  under poor links. H.264 AIMD does not currently adjust Progressive quality.

### Phosh installation requested by the operator

The existing `[packages.phosh]` section was already enabled. The host is a writable
Fedora 44 filesystem, so the authorized native DNF install completed directly:
Phosh 0.57.0, Phoc 0.57.0, Mobile Settings 0.57.0, Stevia 0.57.0, feedbackd and
Calls, with dependencies. Only stable repositories were used for the transaction;
updates-testing was disabled for this command. No installed packages were removed
or upgraded. Mobile Settings was added to the existing SSOT package set so image
builds retain it. No default desktop selection was changed.

`/usr/share/wayland-sessions/phosh.desktop` launches `/usr/bin/phosh-session`;
the shell executable is `/usr/libexec/phosh` (there is no `phosh` command on PATH).
`phosh-session --version`, `/usr/libexec/phosh --version`, `phoc --version`, RPM
versions and `rpm -V phosh phoc phosh-mobile-settings` verified successfully.
The greeter indexes `/usr/share/wayland-sessions`, so Phosh is available for a
future login. The installed shell rendered its app grid successfully in a private
headless Phoc session with Pixman, an isolated D-Bus, in-memory GSettings and
private XDG config/data/cache/state directories. Screenshot:
`/var/tmp/mios-phosh-preview/phosh-headless-preview.png`; receipt:
`/var/tmp/mios-phosh-install/preview-receipt.json`. Wayland-nested Phoc previews
configured an output but screenshot capture stalled, with both GPU and software
renderers; that nested path remains unresolved. A full Phosh/GNOME login on the
physical display was not tested. Only the owned preview units were stopped after
verification; the MiOS nested desktop and operator sessions were preserved. The current
desktop/application identities were preserved; no reboot or session restart.
Receipt: `/var/tmp/mios-phosh-install/receipt.json`, log in the same directory.
The DNF offline update was already invalid before installation; its metadata was
backed up there. It was not repaired or executed as part of this installation.

### Historical checkpoint details follow

## Authorization and preservation

The operator already authorized applying completed, verified changes LIVE. Do
not ask again for that scope. Do not reboot, change drivers/boot selection,
push, discard worktrees or reset the root index. `/` is the deployed repository.
There are unrelated staged runtime/database changes: stage/commit explicit paths
only. Build inside `podman-MiOS-DEV`, with both locks in this order:

```
flock /home/mios/mios-desktop-work-1/heavy.lock \
  flock /var/lib/mios-build/heavy.lock podman exec podman-MiOS-DEV ...
```

Use two build jobs, `CARGO_INCREMENTAL=0`, and retain rollback/source artifacts.
The build volume has about 4 GB free; the root volume has much more space.
The container previously stopped with OOM137 and was restarted; verify it first.
Use native MiOS-MCP relay and private tmux tools for all coordination/terminal work.

## Live state at handoff — verify before changing

* Primary Hyprland PID `5764`, signature
  `release-v0.56.2_1793300043_867081329`, `/run/user/1000`, `wayland-1`.
  The host restarted at 14:53 EDT outside the outgoing turn; older PID10146 and
  earlier signatures/runtime directories are obsolete.
* Input daemon PID `912489`, SHA256
  `fb7af979a74559c3be6baeb736d3d69b67de72d34acde3804576ca61568848a7`.
  Both `/usr/bin/mios-inputd` and `/usr/libexec/mios/mios-inputd` contain it.
* Hypr3D candidate is now loaded on the primary and installed in both
  `/usr/lib64/mios/hypr3d.so` and `/usr/lib64/hyprland/hypr3d.so`, SHA256
  `45f6c23eaee1d9e81f128dee3c681e472437db74ffba00b08e750a0fa9b67730`.
  Room mode was inactive after deployment. The old library was `042f70fe...`.
* Primary Quickshell PID `835676`, installed Fedora version0.2.1 revision
  `dacfa9de829ac7cb173825f593236bf2c21f637e`. Host Qt is6.11.2 Fedora44;
  its matching devel package is installed on the HOST, not in the builder.
* Native RDP PID `496912`, live binary SHA begins `828b6f58`. It has the bounded
  TCP queue changes. Final source-matching candidate is **not yet activated**:
  `/var/lib/mios-build/rdp-delivery/mios-bitmap-queue-candidate`, server SHA256
  `48904af5da31f2f83927bd1010410b7c200fca00a392f3b68943f83e714dc945`.
* Non-secret `/etc/mios/mios.toml` now persists `[remote.rdp] server="native"`
  across reboot, mode0644. It is an ignored host override, not a tracked vendor
  configuration. Restart had previously reverted the tmpfs selection to xrdp.
* Installed earlier: miosd SHA8c5dfa3b..., mios-gen4b2e0282.... Do not restart
  unrelated services just to replace mapped executables.

## Completed this checkpoint: input ownership

The operator's corrected contract is:

* **Tap north**: focus the aimed window and transfer all inputs to its contents
  while staying in the 3D room, including Theatre viewing.
* **Hold north**: enter native 2D fullscreen and capture the window.
* **Hold L3+R3**: release ownership back to the desktop/session.
* While captured, both sticks, triggers, face buttons, bumpers and ordinary
  MiOS actions must belong to the app, without simultaneous room movement.

Rust inputd now distinguishes tap/hold at
`keybindings.global.controller.capture_hold_ms` (500ms default; HTML exposed),
holds local ownership across a stale context snapshot while the native
transaction completes, emits `input:window on|fullscreen|off`, and recognizes
windowed room capture. Captured Guide combinations also keep their analog axes.
A repaired missing north release never invents a capture action. Existing
EVIOCGKEY reconciliation remains in place. A short L3+R3 chord belongs to the
application; the documented hold releases it.

The renderer exposes:

```
hl.plugin.hypr3d.input_owner()                 -- integer native window ID, 0 for session
hl.plugin.hypr3d.input_capture("on")           -- stay in room
hl.plugin.hypr3d.input_capture("fullscreen")   -- native 2D handoff
hl.plugin.hypr3d.input_capture("off")          -- release/return
```

It tracks a weak focused-window owner independently of keyboard/render modes,
quiesces controller movement/actions during ownership, rejects direct room
move/camera input while owned, forwards keyboard/pointer interaction to the
selected surface, and prevents controller fullscreen callbacks from firing
host actions during capture. Rust transactions also update the configurable
native capture tag for compositor keyboard-bind contexts. API absence is an
error, not a guessed successful handoff.

Evidence:

* `/var/lib/mios-build/mios-inputd/mios-ownership-final-build.log`:56 Rust tests;
  renderer CTest2 passed.
* `MIOS_INPUTD_BIN=/var/lib/mios-build/mios-inputd/mios-inputd
  MIOS_INPUTD_UINPUT_TEST=1 python3 /tests/test-mios-inputd.py`:15 passed.
* `/var/lib/mios-build/mios-inputd/mios-ownership-native.json`: owned nested
  PiP test verifies tap stays in room, an arbitrary Qt client becomes
  app-capture, native move/camera attempts leave the camera unchanged,
  hold enters native fullscreen, release returns, invalid mode rejected.
* `mios-render-handoff-native.json`: exact old042f70fe→new45f6c23e plugin
  handoff proven in an isolated child with identities and camera preserved.
* `mios-ownership-live-activation.json`: primary deployment/rollback receipt.
  Initial verification raced the service ExecStartPre and temporarily restored
  old files; `/var/tmp/mios-input-ownership-live-resume.py` corrected the state
  with a real PID/executable wait. Final hashes above are the active versions.
  An early receipt misnamed its false post-deployment room-active value as
  `room_mode_preserved`; it is corrected to `room_active_after`.
* Full12-patch series applies from upstream9301dcf and produces exactly the
  candidate main.cpp. CRT tests15 passed. No broad standing-gate rerun this turn.

Sources: root Rust inputd; renderer source in
`/.worktrees/mios-pip-theatre-source` (base0d26531 after11 patches, dirty final
delta) and `/var/lib/mios-build/mios-pip-theatre/source`. Root ships the final
delta in `usr/share/mios/hyprland/patches/hypr3d-mios-pip-theatre.patch`.
Regenerate from that source diff, normalize blank unified context lines, and
verify the whole series. Do not update one source copy without the other.

Physical gameplay/VM/VR handoff remains operator verification, not certified by
the Qt fixture. Per-client VM device assignment and native VT controller routing
are still open. Do not infer that every guest has a controller from host focus.

## Completed mitigation: HUD bounds and destructive shell lifecycle

At17:25 EDT both physical monitors had reserved=[0,0,0,0]; tiled browser and
terminal extended behind the HUD. Shell logs explicitly confirmed frozen Qt
property bindings, the known HyprlandToplevel update-group leak.

**Important incident:** restarting only mios-shell restored44px top/72px bottom,
but default `KillMode=control-group` also terminated native clients/tmux servers
launched in its cgroup. The outgoing participant caused this, acknowledged it
to the operator, and apologized. Some native windows and the owned test desktop
closed. The compositor, Flatpak clients and persisted source/transcripts
survived; the operator resumed the terminal/returning participant. Do not claim
that restart preserved all applications.

Canonical SSOT and rendered `/usr/lib/systemd/user/mios-shell.service` now set
**KillMode=process**. User-manager daemon-reload applied it without another
restart; shellPID835676 stayed unchanged. This stops shell recovery from
signalling its launched clients. Real two-sided isolated user-unit tests prove
process mode preserves a launched child and old control-group mode kills it:

```
runuser -u mios -- env XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  MIOS_SHELL_LIFECYCLE_LIVE=1 python3 /tests/test-shell-reserve.py TestShellLifecycle
```

Both pass. Prefer separate native application scopes for launchers eventually;
do not revert process-only shutdown before every app is outside the shell unit.
Before any future shell recovery, check its LOADED KillMode, not just the file.

**Underlying freeze is still open.** Existing bake66 and
`src/quickshell/check-update-groups.py` already carry an older-ref fix but were
never applied to the installed Fedora0.2.1. Upstream latest stable is0.3.2,
tag commit4f508be500dea6e5732cc3d50382a0048b17e7b1. Build a staged package against
the exact host Qt ABI, validate in an owned nested shell, then replace live
Quickshell safely. Builder currently lacks Qt pkg-config/devel modules.
Do not run bake66 blindly: it deletes a fixed /tmp build tree, installs deps,
and can use unlimited ninja jobs. Use an owned staging path, two jobs, both
locks and no host library/driver upgrade. No upstream source checkout has been
created for this task yet. Existing EdgeReveal diagnoses the freeze but cannot
repair frozen C++ bindings; nudging/recreating layer surfaces is insufficient.

HUD diagnostic helper: `/var/tmp/mios-hud-workarea-probe.py`. Old IPC calls
without `--any-display` returned no instance despite the running primary shell;
target the actual process/display before assuming an endpoint is absent.

## RDP: root cause found, low FPS NOT fixed

Earlier live samples were about1FPS, completed-write p50~0.7sec with multi-second
TCP stalls. After the host restart xrdp was0FPS; it was switched back to native.
The phone connected to the updated native service for26sec then disconnected
at20:42:56Z without an agent service restart. A question about whether the mobile
app stayed in foreground is pending. Preserve an active connection while
measuring; do not repeat listener restarts merely to reset statistics.

The phone100.89.218.102 uses DERP Toronto, ~90–100ms idle RTT. Its EGFX
capabilities set AVC_DISABLED. Native currently insists on AVC420 readiness and
falls back to legacy bitmap updates. **The real animated1100x2080 desktop
produces1–2.7MB per NSCodec update.** GPU conversion is only~1.4ms. Thus prior
loopback text results (~27KB/frame,52FPS) did NOT validate the phone workload;
do not blame the relay alone or call compression solved. Bounded color
quantization still produced~1.1MB/frame and was removed from production/source.

New source adds RDP-only `tcp_send_buffer_kib=128` and
`tcp_notsent_lowat_kib=16`, validated0..4096, zero preserves kernel defaults.
They bound kernel queueing, not bandwidth. Live process828b6f58 has them.
Final candidate48904af5 also has a diagnostic:

```
mios-rdp-server --bench-capture HEADLESS-MIOS-RDP 5 --hz 5 --bitmap
```

It reports capture FPS (explicit basis), dirty pixels/tiles, and NSCodec size at
diagnostic color-loss level3. These are estimates, NOT measured wire/client FPS.
`fps` in live bitmap stats means completed encoder/socket writes, not scanout.
Live `bitrate_kbps` still counts H264 bytes only and is misleadingly0 in bitmap
mode; correcting that is open.

Final candidate:42 Rust tests passed, one ignored capture fixture separately
passed; backend37 passed with4 explicitly skipped live/operator checks;
OpenH264/EGL/invariant self-tests pass. Root Cargo.lock now includes the direct
NSCodec dependency. Receipts under `/var/lib/mios-build/rdp-delivery/`:
`mios-bitmap-final-build.log`, `mios-bitmap-output-probe.json`,
`mios-bitmap-quantization-probe.json`, `mios-bitmap-queue-activation.json`.
Final source-matching candidate has server+priv+build.json+self-test.json. It
has NOT replaced the currently connected production executable.

**Next implementation:** use EGFX RemoteFX Progressive for AVC_DISABLED clients,
instead of discarding the negotiated graphics pipeline. Upstream already has
`ironrdp_graphics::progressive::{rgba_to_ycbcr,encode_first_pass}`,
`ironrdp_pdu::codecs::rfx::progressive::encode_progressive_stream`, and
`GraphicsPipelineServer::send_remotefx_progressive_frame`. Native readiness and
pipeline code are in `src/mios-rdp-server/.../pipeline.rs`; inspect capability
rules, implement valid streams and decoding tests, bound frames with real EGFX
acks, and test the actual phone. No Progressive implementation has been written.
Use official protocol documentation. Do not enable unsupported UDP or change
firewall/tailnet topology as a guess.

## Incoming participant's parallel work

`mios-desktop-repair-session` acknowledged the17:00 EDT assignment and four-day
update. Its Controls TUI draft is committed on **mios-controls-app @a26211215**,
worktree `/.worktrees/mios-controls-app`, not merged or installed. Use the
worktree's owner, or per-command Git safe.directory; do not change global trust.

* New `tools/native/mios-controls/**`, `tests/test-mios-controls.py`,
  `usr/share/doc/mios/controls.md`; branch-only workspace/Cargo.lock/ignore deltas.
* Static musl candidate
  `/var/lib/mios-build/tmp/mios-controls-app/target/x86_64-unknown-linux-musl/release/mios-controls`,
  SHA prefix50f5dae2c2058c91. Reported31 unit tests (including14 reused gate
  tests),10 real CLI/TUI tests, and a mutant that makes3 tests fail.
* TUI diagrams cover pad/keyboard/peripherals; dedicated input thread; workers
  validate/render without blocking input. Arrows/Enter/Escape/Tab/PgUp/PgDown
  and SGR mouse are the terminal navigation vocabulary.
* Integrate shared SSOT/HTML selectively; do not copy an older whole SSOT over
  current RDP/input/lifecycle changes. Add proposed
  `[keybindings.global.controls] input_cpu=-1, backup_suffix="controls-backup",
  mios_gen="/usr/bin/mios-gen"`, configurator inputs and installation/audit scope.
* It reuses the root's previously uncommitted gate `keybinds.rs` fixes for
  app-capture/native realizers/generated Lua binds. That reviewed source is part
  of the outgoing checkpoint; do not regress it to the earlier HEAD copy.

Approved parallel `mios-tmux-os-improve` workflow/worktree:
`/.worktrees/mios-tmux-os`, owns `usr/share/mios/tmux/*`, the tmux projection in
`tools/native/mios-gen/src/terminal.rs`, `miosd/src/sessions.rs`, dedicated tests,
and branch-local `[keybindings.tmux]`/`[terminal.*]` tables. It adds a TUI-apps
session-selector menu and preserves all existing sessions. No competing lane
currently holds those files. New app/tmux drafts have no live reload permission
until verified integration, but the operator's general deployment authorization
still applies to completed work.

The `mios-bindings-finalize` workflow stages audit/design/critique/final specs
in `/home/mios/mios-desktop-work-1/mios-bindings-final/`; it must integrate the
ownership contract above rather than redesign it. Inputd/plugin code was held
by the outgoing participant; that hold transfers to the incoming lead now.

The operator also told the incoming participant: native VT/console gpm cursor
does not interact with tmux-os; controller should navigate terminal/TUI menus
in desktop terminals AND VT/SSH. gpm is installed/active and tmux mouse is on,
but that is not a working bridge. Investigate existing
`/.worktrees/mios-terminal-mouse` contributions before adding another path.
Likely need a Rust VT PTY bridge that turns gpm/evdev/controller events into
SGR1006 reports only while the child enables mouse modes, plus keyboard
navigation. The incoming participant was authorized to claim this after the
Controls/Tmux draft, with exact disjoint source scope and two-sided fixtures.

## Remaining operator backlog

* Some games black-screen when resized and spawn offscreen instead of at the
  centered global DPI-aware opening dimensions. Inspect real game metadata,
  logical monitor workarea/scale and native configure/swapchain updates; this
  is NOT fixed or certified by the HUD recovery.
* Actual game/VM/controller and VR ownership checks, per-session/peripheral
  assignment, VT controller/menu parity and interruption/stutter checks.
* Global application menu blobs for every app that exports menus, unified glass
  curves/radii/lighting, less sensitive controls proximity, centered HUDs,
  visible scroll-volume percentage and battery/device icons.
* Maximize within reserved HUD bounds; only true fullscreen autohides; foreground
  windows remain interactive above maximized/fullscreen content.
* Full ShaderGlass profile/window-state persistence across all2D/3D modes;
  actual Wine-selected profile/menu behavior remains unproven. Earlier synthetic
  texture-bridge checks and snapshot-alpha fixes do not certify the whole path.
* Automatic Spotify/Discord PiP detection, native stay-on-top, collision packing,
  minimum/maximum size snapping, Theatre/fullscreen matrix, nested full-frame
  scale and multiplayer/VR device assignment. New lab was PiP by default but
  closed during the shell lifecycle incident; leave future test desktops alive.
* Blender/MCP request: locate the installation receipt and verify current state;
  do not assume this outgoing turn completed it.

## Coordination and continuation order

The correct relay is the operator uid1000 registry; the MCP server's root
`mios_agent_list` can show an unrelated stale roster. Existing helper:

```
runuser -u mios -- env MIOS_RELAY_AGENT_ID=<your-own-identity> \
  /usr/lib/mios/mcp/.venv/bin/python3 /var/tmp/mios-desktop-relay.py \
  receive
```

It also supports `register`, `list`, `observe`, `ack ID`,
`send RECIPIENT ID BODYFILE`, and `status NOTE`. Use your own private token;
never impersonate the predecessor. Outgoing registration's token is private in
`/run/user/1000/mios/mios-rdp-live-recovery-lease.json`. Do not copy it.

Recommended next order: acknowledge this handoff and own the remaining lanes;
integrate Controls/Tmux source safely; verify physical capture while keeping
apps alive; fix and build the Qt shell; implement EGFX Progressive and measure
phone delivery; investigate game resize/placement and remaining mode-state
gaps. Preserve all receipts and report limitations. A queued message is not
an acknowledgement or completed work. The broad goal must remain open.
