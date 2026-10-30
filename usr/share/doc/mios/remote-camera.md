<!-- AI-hint: Native RDPECAM camera forwarding, client consent, PipeWire consumers, lifecycle and reproducible synthetic verification. -->
<!-- AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_camera.rs, src/mios-rdp-server/prepare.rs, usr/share/mios/mios.toml, tests/fixtures/mios-rdp/camera.py -->

# Remote cameras

The native MiOS RDP server accepts cameras explicitly selected and permitted by a compatible client through MS-RDPECAM. Each camera becomes a separate PipeWire `Video/Source` node named `mios-remote-camera-<server-pid>-<generation>`, with the description `MiOS-Remote-Camera`. Device names, negotiated formats, native node IDs, activity, forwarded frames/bytes and errors appear in `device_forwarding.camera` in the session's statistics JSON.

On iOS/iPadOS Windows App, enable **Settings → App Permissions → Camera**, then edit the remote PC and enable **Device & audio redirection → Camera**. The device's OS permission must also allow camera access. Reconnect after changing redirection settings. These controls are documented in [Windows App device redirection](https://learn.microsoft.com/en-us/windows-app/device-audio-folder-redirection-teams#remote-pc-redirection).

Select the source in a PipeWire camera consumer. PipeWire-aware applications and camera portals can consume it; applications that require a `/dev/video*` V4L2 device need an additional V4L2 bridge, which this implementation does not provide. A missing client camera channel produces no native source. Server permission does not override client consent.

## Capture and cleanup

The server activates the client device to discover its color stream and primes the video pipeline so the native source can be published. This can briefly activate the client's camera when it connects. Once published, capture deactivates while no native consumer is using the source and resumes when a consumer opens it. Closing a consumer, removing a device, closing the enumeration channel and disconnecting the RDP session have explicit cleanup paths. The server neither changes the default camera nor records physical camera frames to disk.

H.264 and MJPEG use native GStreamer decoders. Packed NV12, I420 and YUY2 are also supported. The default negotiated bounds are 1280×720 at up to 30 FPS, with four camera announcements allowed per connection. These are ceilings, not delivered FPS claims. The protocol uses one outstanding sample request and bounded decoder pipes; malformed samples close the affected camera channel. Native graph queries have bounded output and timeouts. A client that offers only unsupported formats or resolutions reports an error rather than a working empty camera.

The native video node belongs to the operator's PipeWire runtime even when its RDP display uses an isolated nested compositor. All provider processes and sources are owned by their RDP connection. The server waits for owned camera/audio/filesystem workers during shutdown.

## Configuration

Edit the layered MiOS SSOT through the configurator. Camera settings are:

| SSOT key | Purpose |
| --- | --- |
| `remote.rdp.redirect.camera` | Permit client-selected camera forwarding. |
| `remote.rdp.native.camera_video_program` | GStreamer video provider executable. |
| `remote.rdp.native.camera_graph_program` | Native PipeWire graph inspection executable. |
| `remote.rdp.native.camera_max_width`, `camera_max_height` | Negotiated resolution bounds. |
| `remote.rdp.native.camera_max_fps` | Negotiated source-rate ceiling. |
| `remote.rdp.native.camera_max_devices` | Camera announcement bound per connection. |
| `remote.rdp.native.camera_poll_ms` | Consumer activity polling interval. |
| `remote.rdp.native.camera_timeout_ms` | Startup and active frame timeout. |
| `remote.rdp.naming.camera_source_prefix` | Per-camera native node name prefix. |

A server update takes effect after the native RDP listener restarts. Existing connections continue using their current binary until then. The desktop compositor and application windows do not require a restart.

## Verification

`tests/fixtures/mios-rdp/camera.py` runs an opt-in compiled Rust fixture as the desktop operator. Pass its `--test-binary` argument the isolated builder's Rust unit-test executable, set `MIOS_RDP_CAMERA_LIVE=1`, and choose a new `--report` path. It sends actual encoded enumeration/device/DRDYNVC messages through the protocol processors and reads changing synthetic frames through real native PipeWire consumers. It exercises raw video, H.264, MJPEG, permission denial, idle deactivation, reopening, device removal, enumeration close and connection cleanup. It does not use a TCP/TLS session or record a physical camera; mobile client interoperability must be verified separately against the live listener.

The static Rust source preparer adds guarded connection-local dynamic-channel extension hooks to the upstream server before compilation, alongside the negotiated RDPDR logon fix. Existing graphics, input, microphone and static-channel processors stay attached. Source preparation rejects unknown upstream boundaries and has idempotence and negative controls.

Protocol reference: [Microsoft MS-RDPECAM](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpecam/). Native video provider: [PipeWire GStreamer sink](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/src/gst/gstpipewiresink.c).
