<!-- AI-hint: Complete upstream source replacements and verified deployment receipts for Linux Looking Glass capture and hover input. -->
# Linux capture and hover input

Upstream source: https://github.com/gnif/LookingGlass/commit/cdba29f2f6bcae3ded26369fc231a067cbca3752
The complete replacement files retain the upstream GPL notices.

Copy these replacements into that upstream checkout before configuring CMake:

| Replacement | Upstream destination |
| --- | --- |
| host-pipewire.c | host/platform/Linux/capture/pipewire/src/pipewire.c |
| host-portal.c | host/platform/Linux/capture/pipewire/src/portal.c |
| host-app.c | host/src/app.c |
| host-capture.h | host/include/interface/capture.h |
| client-wayland-input.c | client/displayservers/Wayland/input.c |

Build inside podman-MiOS-DEV. Configure the host and client in separate build directories.
The client uses libdecor and PipeWire audio. Guest capture uses PipeWire and /dev/kvmfr0;
the host viewer reads /dev/shm/looking-glass and uses SPICE port 5930 on this deployment.
These device identities and frame rate come from the deployed VM profile.

The host requests a cursor metadata size range that includes KDE's 256 by 256 buffers.
KDE RGBA cursor pixels convert to the BGRA color format expected by the viewer.
Cursor-bearing queued frames must survive until their shape has been consumed.
Metadata publication begins after LGMP queue initialization; buffer teardown obeys the
PipeWire thread-loop lock. A disconnected viewer retains the portal session.

The Wayland viewer translates unlocked surface motion into relative input for providers
such as SPICE. Locked pointers and providers supporting absolute positions retain their
existing input path. This fixes clicks working while hover movement sends nothing.

Compile test-cursor.c against the host target's CMake flags and generated headers.
Define MIOS_LOOKING_GLASS_PIPEWIRE_SOURCE as the absolute upstream pipewire.c path.
Compile test-hover.c against the Wayland target's flags and generated protocol headers.
Define MIOS_LOOKING_GLASS_CLIENT_INPUT_SOURCE as the absolute upstream input.c path.
Keep assertions enabled with -UNDEBUG and use function/data sections plus --gc-sections.
The hover fixture fails against the original upstream callback and passes against the
replacement; locked and absolute-input controls reject duplicate movement.

Verified on 2026-10-21:
- Host capture build and BGRA/RGBA plus seven malformed/missing-metadata controls passed.
- Viewer release build and hover regression passed inside the development builder.
- The original callback failed the movement assertion as the negative control.
- The operator confirmed mouse movement working after the viewer replacement.
- Cursor position and hotspot updates were observed in the connected viewer.
- The existing Bazzite desktop later locked; unlocking its authenticated session restored it.

Installed capture SHA256: 46237aee68537771890f8b83f48f2ede688f5641e6d3f31d994f908cb22326ef
Installed viewer SHA256: 3a1478ebaf1ebacf2ef1241f96def7d19308c9349ccaf26ff065e4913cbea643

Deployment state: capture is a persistent guest user service using a user-owned binary;
the host viewer is installed at /usr/bin/looking-glass-client. The base guest image and
the standard client bake script have not yet been rebuilt/integrated with these sources.
A future image rebuild must copy these replacements before compilation; the retained
base image alone does not contain these fixes. Cold-boot portal restoration remains open.
