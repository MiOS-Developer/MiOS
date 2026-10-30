<!-- AI-hint: Native RDPECAM camera forwarding, client consent, which consumers see a remote camera (PipeWire, OBS, camera portal pickers, V4L2 via mios-camera-v4l2), lifecycle, exclusive capture, provider restarts and reproducible synthetic verification. -->
<!-- AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_camera.rs, src/mios-rdp-server/prepare.rs, usr/share/mios/mios.toml, usr/bin/mios-camera-v4l2, tests/fixtures/mios-rdp/camera.py, tests/fixtures/mios-rdp/obs.py -->

# Remote cameras

The native MiOS RDP server accepts cameras explicitly selected and permitted by a compatible client through MS-RDPECAM. Each camera becomes a separate PipeWire `Video/Source` node internally named `mios-remote-camera-<server-pid>-<generation>`; process identifiers stay in that internal ownership name. Its picker label is the client's short device name followed by `(MiOS RDP)`, for example `Front Camera (MiOS RDP)`. Device names, negotiated formats, native node IDs, activity, capture state, forwarded frames/bytes, provider restarts, the consumers that can see the camera and errors appear in `device_forwarding.camera` in the session's statistics JSON.

On iOS/iPadOS Windows App, enable **Settings → App Permissions → Camera**, then edit the remote PC and enable **Device & audio redirection → Camera**. The device's OS permission must also allow camera access. Reconnect after changing redirection settings. These controls are documented in [Windows App device redirection](https://learn.microsoft.com/en-us/windows-app/device-audio-folder-redirection-teams#remote-pc-redirection). A missing client camera channel produces no native source. Server permission does not override client consent.

## Which applications see the camera

`device_forwarding.camera.consumers` (and the `consumers` field of every entry in `sources`) states which consumer families can see a published camera:

| Field | Consumers | How |
| --- | --- | --- |
| `pipewire` | PipeWire-native applications (GStreamer `pipewiresrc`, `pw-cat`-style tools) | Always: the node is a regular `Video/Source`. |
| `camera_portal` | Camera pickers that go through xdg-desktop-portal's Camera portal or WirePlumber's portal access: OBS **Video Capture Device (PipeWire)**, Flatpak apps (for example GNOME Snapshot), WebRTC in MiOS-Portal, Firefox and Chromium | Always: the node carries `media.role=Camera`, the property those pickers filter on. |
| `v4l2` | Legacy applications that only open `/dev/video*` | `true` when `remote.rdp.native.camera_v4l2` is enabled and `camera_v4l2_library` (package `pipewire-v4l2`) is installed. `v4l2_reason` explains a `false`. |

MiOS has no camera picker of its own: its desktop camera pickers are these portal and PipeWire pickers. MiOS-Portal enables WebRTC PipeWire cameras through `[desktop.portal.gecko].pipewire_camera` (`media.webrtc.camera.allow-pipewire`). Chromium-based browsers need their PipeWire camera option (`chrome://flags/#enable-webrtc-pipewire-camera`) where it is not already the default.

### OBS

In OBS, add **Video Capture Device (PipeWire)** (called **Video Capture Device (PipeWire) (BETA)** in some versions), then select the remote camera. The installed MiOS OBS PipeWire plugin includes this source. OBS's V4L2 camera source uses a separate device list and does not enumerate a PipeWire camera unless OBS itself is started through `mios-camera-v4l2`. OBS implements its camera connection through the camera portal; see the [upstream camera source](https://github.com/obsproject/obs-studio/blob/master/plugins/linux-pipewire/camera-portal.c).

### V4L2-only applications

No kernel module is loaded. Start the application through the per-app launcher:

```sh
mios-camera-v4l2 <program> [arguments...]
mios-camera-v4l2 --check        # active, or the reason it is not
```

The launcher preloads PipeWire's V4L2 compatibility library. The program's `open("/dev/video0")` and V4L2 ioctls then go to PipeWire cameras. `/dev/video0` is the first PipeWire camera, which is the remote camera when it is the only one. For a desktop entry, prefix its `Exec=` line with `mios-camera-v4l2`. When remote cameras or compatibility are disabled, or the library is missing, the launcher starts the program unchanged and prints why.

Limits: the preload does not cross into Flatpak or other sandboxes (use the camera portal there). Statically linked programs and programs that discover cameras only by listing `/sys/class/video4linux` cannot be redirected. One V4L2 open is one PipeWire stream.

### Native format

The decoded PipeWire output is packed single-plane BGRA, at the negotiated rectangle. The client-to-server camera stream can still use compressed H.264; BGRA is only the local decoded format. One format serves every consumer family:

- OBS 32.1.1's PipeWire camera format table does not accept I420, even though other PipeWire consumers do; see its [supported formats](https://github.com/obsproject/obs-studio/blob/32.1.1/plugins/linux-pipewire/formats.c). NV12 appears in that table, but its [asynchronous frame-copy path](https://github.com/obsproject/obs-studio/blob/32.1.1/plugins/linux-pipewire/pipewire.c) mishandles the tested portrait planar buffers and crashes. Packed BGRA uses one correctly sized plane and passes an installed-libobs copy test.
- PipeWire's V4L2 compatibility library negotiates single-plane buffers. A planar I420 source fails buffer allocation, and an NV12 source does not deliver frames to an `ffmpeg -f v4l2` consumer. BGRA reaches V4L2 consumers as a 32-bit packed format.

The backend continues priming until the source advertises usable video dimensions and a positive frame rate. A node's mere appearance is insufficient: stopping earlier can leave a visible camera name with no selectable formats.

## Capture and cleanup

The server activates the client device to discover its color stream and primes the video pipeline so the native source can be published. This can briefly activate the client's camera when it connects. Once published, capture deactivates while no native consumer is using the source and resumes when a consumer opens it. Closing a consumer, removing a device, closing the enumeration channel and disconnecting the RDP session have explicit cleanup paths. The server neither changes the default camera nor records physical camera frames to disk.

Capture ownership is serialized across cameras announced by one client, because phones usually cannot capture from two cameras at once. While one remote camera streams, another selected camera reports `"capture": "waiting"` in its source entry. It starts when the first one deactivates. Waiting does not count as a frame timeout, and serializing avoids simultaneous priming taking the physical camera away from the selected stream.

A consumer whose PipeWire link fails can make the provider process exit. The server then restarts the provider, up to `camera_provider_restarts` times in a row (default 3, counted again after 30 s of healthy running). It also restarts the client stream so the decoder receives a fresh key frame. `0` closes the camera on the first exit, as before restarts existed. Restarts are counted in `device_forwarding.camera.provider_restarts`.

H.264 and MJPEG use native GStreamer decoders. Packed NV12, I420 and YUY2 inputs are also supported. The default negotiated bounds are 1280×720, or 720×1280 in portrait orientation, at up to 30 FPS, with four camera announcements allowed per connection. These are ceilings, not delivered FPS claims. Decoded video is normalized to the negotiated rectangle while preserving aspect ratio. Streams that omit usable coded or decoded frame timing receive the negotiated timing. This normalization also handles clients that send a larger coded rectangle than they advertised.

The protocol uses one outstanding sample request and bounded decoder pipes; malformed samples close the affected camera channel. Native graph queries have bounded output and timeouts. A client that offers only unsupported formats or resolutions reports an error rather than a working empty camera. Late cancellation replies after a deliberate stop do not invalidate an idle source.

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
| `remote.rdp.native.camera_provider_restarts` | Provider restarts after a decoder exit, 0..8. |
| `remote.rdp.native.camera_v4l2` | Offer the camera to V4L2 applications through `mios-camera-v4l2`. |
| `remote.rdp.native.camera_v4l2_library` | Absolute path of PipeWire's V4L2 compatibility library. |
| `remote.rdp.native.camera_pipewire_runtime` | Empty: the operator's PipeWire. An absolute runtime directory targets another PipeWire instance (private labs). |
| `remote.rdp.naming.camera_source_prefix` | Per-camera native node name prefix. |
| `desktop.portal.gecko.pipewire_camera` | MiOS-Portal WebRTC lists PipeWire (portal) cameras. |

A server update takes effect after the native RDP listener restarts. Existing connections continue using their current binary until then. The desktop compositor and application windows do not require a restart.

## Verification

`tests/fixtures/mios-rdp/camera.py` runs an opt-in compiled Rust fixture as the desktop operator against a private lab PipeWire, never the operator's session graph. Pass `--test-binary` (the isolated builder's Rust unit-test executable) and `--pipewire-runtime` (the lab runtime directory with its own PipeWire and WirePlumber). Set `MIOS_RDP_CAMERA_LIVE=1` and choose a new `--report` path. Optional consumers:

- `--v4l2-command` and `--v4l2-off-command` add an `ffmpeg -f v4l2` consumer. Each is a JSON argv prefix, for example a `bwrap --dev /dev` sandbox around `mios-camera-v4l2`, so no physical `/dev/video*` is reachable. The second prefix uses an SSOT layer with `camera_v4l2 = false`.
- `--picker-command` adds the picker check: a JSON argv printing the camera portal's `IsCameraPresent`, from a portal on a private bus.
- `--obs` runs `tests/fixtures/mios-rdp/obs.py` against the portrait H.264 camera. The installed OBS PipeWire plugin must copy twelve distinct frames into libobs and shut down cleanly. It initializes its own libobs context on the lab session's Wayland display and does not modify the user's scenes. The separate `obs.py --controls --report PATH` mode runs a portrait NV12 negative control and a BGRA positive control in private process groups, with core dumps disabled.

The fixture sends actual encoded enumeration/device/DRDYNVC messages through the protocol processors and reads changing synthetic frames through real consumers. It covers raw video, H.264 (landscape, portrait, transposed SPS and a larger coded rectangle) and MJPEG. It also covers permission denial, disabled forwarding, idle deactivation, reopening, device removal, enumeration close and connection cleanup. Tests inspect the advertised BGRA format before connecting a consumer and let the consumer negotiate dimensions.

Positive cases require frames in the PipeWire, OBS and V4L2 consumers and a picker-visible camera-role node. The disabled and denied cases require no picker node, no portal camera, no native frames and no V4L2 frames. Two negative controls cover the other features. `h264-v4l2-off` requires the V4L2 consumer to find no camera while PipeWire consumers keep working. `h264-provider-no-restart` kills the provider with no restart budget and requires the camera to close; its positive twin `h264-provider-restart` requires the same kill to be survived. No case uses a TCP/TLS session or saves physical camera frames.

Physical phone interoperability is the operator's step. Connect Windows App with camera redirection enabled, then open a camera picker: the OBS PipeWire camera source, a MiOS-Portal WebRTC page, GNOME Snapshot or `mios-camera-v4l2 ffplay -f v4l2 /dev/video0`. Check `device_forwarding.camera.sources` for the negotiated format, `capture` state and nonzero `frames_forwarded`.

The static Rust source preparer adds guarded connection-local dynamic-channel extension hooks to the upstream server before compilation, alongside the negotiated RDPDR logon fix. Existing graphics, input, microphone and static-channel processors stay attached. Source preparation rejects unknown upstream boundaries and has idempotence and negative controls.

Protocol reference: [Microsoft MS-RDPECAM](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpecam/). Native video provider: [PipeWire GStreamer sink](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/src/gst/gstpipewiresink.c). V4L2 compatibility: [pipewire-v4l2](https://gitlab.freedesktop.org/pipewire/pipewire/-/tree/master/pipewire-v4l2).
