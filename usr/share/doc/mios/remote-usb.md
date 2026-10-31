<!-- AI-hint: Operator guide for generic USB forwarding: RDP MS-RDPEUSB (URBDRC) bridged to an already-loaded USB/IP vhci_hcd port, the shared [remote.device_forwarding] allow/deny policy, per-session target selection, the SPICE usbredir filter and the capability report across RDP, USB/IP, SPICE and nested sessions. -->
<!-- AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_usb.rs, src/mios-rdp-server/crates/mios-rdp-priv/src/main.rs, tools/native/mios-gen/src/device_forwarding.rs, usr/share/mios/mios.toml, usr/lib/systemd/system/mios-rdp-usb.socket, tests/test-device-forwarding.py -->

# Remote USB forwarding

MiOS forwards a remote client's generic USB device into the live session only
when three things are true: the server permits it, the session's selected target
allows it, and the device passes the shared allow/deny policy. Everything is off
by default.

## Which clients can offer USB

Generic USB redirection (MS-RDPEUSB, "RemoteFX USB") is offered only by Windows
clients: `mstsc` and Windows App on Windows. Microsoft's
[platform matrix](https://learn.microsoft.com/en-us/windows-app/compare-platforms-features)
lists no USB port redirection for Windows App on macOS, iOS/iPadOS,
Android/ChromeOS, the web client or Meta Quest. Those clients never announce a
device. Cameras, microphones and drives use their own protocols
(see `remote-camera.md`).

## How a redirected device reaches Linux

```
client device -> URBDRC (RDP) -> mios-rdp-server remote_usb -> USB/IP socket -> vhci_hcd port -> Linux drivers
```

The server translates every URB the kernel sends over USB/IP (`CMD_SUBMIT`,
`CMD_UNLINK`) into URBDRC requests. `SET_CONFIGURATION`, `SET_INTERFACE` and
`CLEAR_FEATURE(ENDPOINT_HALT)` become URBDRC select-configuration,
select-interface and reset-pipe requests. An unlinked URB is cancelled on the
client.

`vhci_hcd` is a kernel module. **MiOS never loads it.** If it is not already
loaded, every announced device is retracted back to the client, and the report
says why. Fedora builds the module (`CONFIG_USBIP_VHCI_HCD=m`), but it ships in
the separate kernel modules package. An administrator who wants USB forwarding
installs that package and loads the module themselves.

Attaching a socket to a vhci port needs root. The root helper
`mios-rdp-priv --usb` handles this on `mios-rdp-usb.socket`. It accepts only the
installed `mios-rdp-server` (the same caller check as the TLS key hand-over),
only while `[remote.rdp.redirect].usb = true`, and only stream sockets. Each
helper connection holds one port. When the connection closes, the helper
detaches exactly that port; it checks the port number and device id first, so a
reused port is never detached. The connection closes when the device is removed,
the client disconnects, or the server exits or crashes. The helper unit keeps
`ProtectKernelModules=yes`, so it cannot load a module either.

## Admission

Each device announced by the client is checked in this order:

1. `[remote.rdp.redirect].usb` must be true.
2. The session target (below) must not be `none`.
3. The announced IDs (`USB\VID_xxxx&PID_xxxx`, `USB\Class_xx`) must pass the policy.
4. The session must hold fewer than `max_devices` devices.
5. The device speed must be full or high speed, and a free high-speed vhci port must exist.
6. The real device and configuration descriptors are read over URBDRC and checked again.

Step 6 runs before the kernel sees the device. A composite device only announces
`USB\COMPOSITE`, so a hidden HID interface is caught here. Any refusal sends
`RETRACT_DEVICE`, and the device stays usable on the client.

## Policy (`[remote.device_forwarding.usb]`)

| key | meaning |
|---|---|
| `allow` | `VVVV:PPPP`, `VVVV:*` or `class:CC` (hex). Empty forwards nothing. |
| `deny` | Same grammar; deny wins. Default: hubs (`09`), HID (`03`) and wireless controllers (`e0`). HID is denied because a forwarded keyboard, mouse or pad injects input into the operator's session. |
| `max_devices` | Devices per session, 1..16. |

The same policy drives SPICE: `mios-gen device-forwarding spice-filter` prints a
usbredir filter (deny rules first, then allow rules, then deny-all). Pass it to
`remote-viewer --spice-usbredir-auto-redirect-filter=...`.

## Sessions and targets

| session | transport | consumer |
|---|---|---|
| `rdp` | `host-vhci` | mios-rdp-server reads it for every RDP connection |
| `nested:<seat>` | `nested-evdev` | input nodes only (`[desktop.nested.devices]`); generic USB is not bound into nested sessions |
| `spice:<domain>` | `spice-usbredir` | `spice-filter --session spice:<domain>` returns a deny-all filter for `none` |

```
mios-gen device-forwarding report            # JSON capability report
mios-gen device-forwarding select --session rdp --target none
mios-gen device-forwarding spice-filter [--session spice:<domain>]
```

`auto` (the default, `[remote.device_forwarding].default_target`) uses the
session's transport when it is available. The choice is stored in
`$XDG_RUNTIME_DIR/mios/device-forwarding/targets.json` (mode 0600, written
atomically).

The report lists the RDP URBDRC path (server permission, vhci state, helper
socket, live per-connection status from `stats.json`), USB/IP (vhci module
state, free ports), SPICE usbredir (libusbredirhost, viewers, ACL helper, QEMU
usb-redirect, libvirt domains with a `spicevmc` redirdev) and live nested
sessions. Libvirt inventory reads only `libvirt_uris` (default
`qemu:///session`) and never inspects a domain that matches `exclude_domains`
(default `mios-xbox*`).

## Live status

`stats.json` `device_forwarding.usb` contains:

- `permitted` and `endpoint` (vhci availability and reason).
- `target`.
- `client.status`: `server_disabled`, `no_devices_offered` or `client_redirects_usb`.
- Every device of the current connection, with its state (`announced`, `preflight`, `attaching`, `attached`, `refused`, `attach_failed`, `detached` or `closed`) and the reason.

Devices from earlier connections are dropped once they stop. A new connection
never inherits another connection's claims.

To redirect from Windows, use `usbdevicestoredirect:s:*`. `mios-rdp rdp-file`
adds this line when `[remote.rdp.redirect].usb` is true.

## Limits

- RDPEUSB v1 has no SuperSpeed: devices attach to high-speed ports.
- No physical device is ever exported from the MiOS host (`usbip-host` is not used).
- The tests never claim a physical device. They use synthetic URBDRC PDUs and a synthetic vhci kernel peer.
