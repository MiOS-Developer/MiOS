<!-- AI-hint: Native RDPECAM camera forwarding, client consent, which consumers see a remote camera (PipeWire, camera portal pickers, V4L2 via mios-camera-v4l2), lifecycle, exclusive capture and reproducible synthetic verification. -->
<!-- AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_camera.rs, src/mios-rdp-server/prepare.rs, usr/share/mios/mios.toml, usr/bin/mios-camera-v4l2, tests/fixtures/mios-rdp/camera.py -->

# Remote cameras

The native MiOS RDP server accepts cameras explicitly selected and permitted by a compatible client through MS-RDPECAM. Each camera becomes a separate PipeWire `Video/Source` node named `mios-remote-camera-<server-pid>-<generation>`. Its picker label is the client's device name followed by `(MiOS RDP)`, for example `Front Camera (MiOS RDP)`. Device names, negotiated formats, native node IDs, activity, forwarded frames/bytes, provider restarts and errors appear in `device_forwarding.camera` in the session's statistics JSON.

On iOS/iPadOS Windows App, enable **Settings → App Permissions → Camera**, then edit the remote PC and enable **Device & audio redirection → Camera**. The device's OS permission must also allow camera access. Reconnect after changing redirection settings. These controls are documented in [Windows App device redirection](https://learn.microsoft.com/en-us/windows-app/device-audio-folder-redirection-teams#remote-pc-redirection). A missing client camera channel produces no native source. Server permission does not override client consent.

## Which applications see the camera

`device_forwarding.camera.consumers` (and the `consumers` field of every entry in `sources`) states which consumer families can see a published camera:

| Field | Consumers | How |
| --- | --- | --- |
| `pipewire` | PipeWire-native applications (GStreamer `pipewiresrc`, OBS PipeWire source, `pw-cat`-style tools) | Always: the node is a regular `Video/Source`. |
| `camera_portal` | Camera pickers that go through xdg-desktop-portal's Camera portal or WirePlumber's portal access: Flatpak apps (for example GNOME Snapshot), WebRTC in MiOS-Portal, Firefox and Chromium | Always: the node carries `media.role=Camera`, the property those pickers filter on. |
| `v4l2` | Legacy applications that only open `/dev/video*` | `true` when `remote.rdp.native.camera_v4l2` is enabled and `camera_v4l2_library` (package `pipewire-v4l2`) is installed. `v4l2_reason` explains a `false`. |

MiOS-Portal enables WebRTC PipeWire cameras through `[desktop.portal.gecko].pipewire_camera` (`media.webrtc.camera.allow-pipewire`). Chromium-based browsers need their PipeWire camera option (`chrome://flags/#enable-webrtc-pipewire-camera`) where it is not already the default.

### V4L2-only applications

No kernel module is loaded. Start the application through the per-app launcher:

```sh
mios-camera-v4l2 <program> [arguments...]
mios-camera-v4l2 --check        # active, or the reason it is not
```

The launcher preloads PipeWire's V4L2 compatibility library, so the program's `open("/dev/video0")` and V4L2 ioctls go to PipeWire cameras: `/dev/video0` is the first PipeWire camera, which is the remote camera when it is the only one. For a desktop entry, prefix its `Exec=` line with `mios-camera-v4l2`. When compatibility is disabled or the library is missing, the launcher starts the program unchanged and prints why.

The V4L2 library negotiates single-plane buffers only. With `camera_v4l2 = true` (the default) the server publishes packed YUY2; planar I420 cannot be linked to a V4L2 consumer. With `camera_v4l2 = false` the server publishes I420, the `v4l2` capability is `false`, and a V4L2 attempt is refused (the provider restarts, the camera stays available to PipeWire consumers).

Limits: the preload does not cross into Flatpak or other sandboxes (use the camera portal there); statically linked programs and programs that discover cameras only by listing `/sys/class/video4linux` cannot be redirected; one V4L2 open is one PipeWire stream.

## Capture and cleanup

The server activates the client device to discover its color stream and primes the video pipeline so the native source can be published. This can briefly activate the client's camera when it connects. Once published, capture deactivates while no native consumer is using the source and resumes when a consumer opens it. Closing a consumer, removing a device, closing the enumeration channel and disconnecting the RDP session have explicit cleanup paths. The server neither changes the default camera nor records physical camera frames to disk.

Phones usually cannot capture from two cameras at once, so capture is exclusive per connection: while one remote camera streams, another selected camera reports `"capture": "waiting"` in its source entry and starts when the first one goes idle. Waiting is not a frame timeout. A consumer whose PipeWire link fails makes the provider process exit; the server restarts it (at most three times in a row) and restarts the client stream so the decoder receives a fresh key frame.

H.264 and MJPEG use native GStreamer decoders; the negotiated frame rate is merged into the parsed stream caps because many mobile bitstreams carry no timing. Packed NV12, I420 and YUY2 are also supported. Portrait cameras use the same pixel bounds as landscape ones, and an H.264 stream whose SPS carries the transposed rectangle is accepted without resizing. The default negotiated bounds are 1280×720 (or 720×1280) at up to 30 FPS, with four camera announcements allowed per connection. These are ceilings, not delivered FPS claims. The protocol uses one outstanding sample request and bounded decoder pipes; malformed samples close the affected camera channel. Native graph queries have bounded output and timeouts. A client that offers only unsupported formats or resolutions reports an error rather than a working empty camera.

The native video node belongs to the operator's PipeWire runtime even when its RDP display uses an isolated nested compositor. All provider processes and sources are owned by their RDP connection. The server waits for owned camera/audio/filesystem workers during shutdown.

## Configuration

Edit the layered MiOS SSOT through the configurator. Camera settings are:

| SSOT key | Purpose |
| --- | --- |
| `remote.rdp.redirect.camera` | Permit client-selected camera forwarding. |
| `remote.rdp.native.camera_video_program` | GStreamer video provider executable. |
| `remote.rdp.native.camera_graph_program` | Native PipeWire graph inspection executable. |
| `remote.rdp.native.camera_max_width`, `camera_max_height` | Negotiated resolution bounds (either orientation). |
| `remote.rdp.native.camera_max_fps` | Negotiated source-rate ceiling. |
| `remote.rdp.native.camera_max_devices` | Camera announcement bound per connection. |
| `remote.rdp.native.camera_poll_ms` | Consumer activity polling interval. |
| `remote.rdp.native.camera_timeout_ms` | Startup and active frame timeout. |
| `remote.rdp.native.camera_v4l2` | Publish single-plane YUY2 so V4L2 applications can use the camera through `mios-camera-v4l2`. |
| `remote.rdp.native.camera_v4l2_library` | Absolute path of PipeWire's V4L2 compatibility library. |
| `remote.rdp.native.camera_pipewire_runtime` | Empty: the operator's PipeWire. An absolute runtime directory targets another PipeWire instance (private labs). |
| `remote.rdp.naming.camera_source_prefix` | Per-camera native node name prefix. |
| `desktop.portal.gecko.pipewire_camera` | MiOS-Portal WebRTC lists PipeWire (portal) cameras. |

A server update takes effect after the native RDP listener restarts. Existing connections continue using their current binary until then. The desktop compositor and application windows do not require a restart.

## Verification

`tests/fixtures/mios-rdp/camera.py` runs an opt-in compiled Rust fixture as the desktop operator against a private lab PipeWire, never the operator's session graph. Pass `--test-binary` (the isolated builder's Rust unit-test executable), `--pipewire-runtime` (the lab runtime directory with its own PipeWire and WirePlumber), set `MIOS_RDP_CAMERA_LIVE=1`, and choose a new `--report` path. Optional `--v4l2-command` (a JSON argv prefix, for example a `bwrap --dev /dev` sandbox around `mios-camera-v4l2`, so no physical `/dev/video*` is reachable) adds an `ffmpeg -f v4l2` consumer; `--picker-command` (a JSON argv printing the camera portal's `IsCameraPresent`, from a portal on a private bus) adds the picker check.

The fixture sends actual encoded enumeration/device/DRDYNVC messages through the protocol processors and reads changing synthetic frames through real consumers. It covers raw video, H.264 (landscape, portrait and transposed SPS), MJPEG, permission denial, disabled forwarding, idle deactivation, reopening, device removal, enumeration close and connection cleanup. Positive cases require frames in the PipeWire and V4L2 consumers and a picker-visible camera-role node; the disabled and denied cases require no picker node, no portal camera and no V4L2 frames; the `h264-v4l2-off` case requires the V4L2 consumer to be refused while the camera survives for PipeWire consumers. It does not use a TCP/TLS session or record a physical camera.

Physical phone interoperability is the operator's step: connect Windows App with camera redirection enabled, open a camera picker (MiOS-Portal WebRTC test page, GNOME Snapshot or `mios-camera-v4l2 ffplay -f v4l2 /dev/video0`), and check `device_forwarding.camera.sources` for the negotiated format, `capture` state and nonzero `frames_forwarded`.

The static Rust source preparer adds guarded connection-local dynamic-channel extension hooks to the upstream server before compilation, alongside the negotiated RDPDR logon fix. Existing graphics, input, microphone and static-channel processors stay attached. Source preparation rejects unknown upstream boundaries and has idempotence and negative controls.

Protocol reference: [Microsoft MS-RDPECAM](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpecam/). Native video provider: [PipeWire GStreamer sink](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/src/gst/gstpipewiresink.c). V4L2 compatibility: [pipewire-v4l2](https://gitlab.freedesktop.org/pipewire/pipewire/-/tree/master/pipewire-v4l2).
