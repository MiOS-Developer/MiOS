<!-- AI-hint: MiOS-Xbox goal: build new MiOS-Xbox Windows image(s) from the current MiOS-Xbox specs and launch them on MiOS hosts; stopping conditions with positive and negative evidence, non-goals. -->
# MiOS-Xbox goal

Operator objective (verbatim, 2026-10-16):

> build MiOS-Xbox image(s) to specs and launch on MiOS host(s) with all drivers/guest tools+features
> etc-etc--builds new; current images are completely wrong and use legacy code that dictates
> incorrectly a left aligned start button(false--old code; not derived from current MiOS-Xbox's
> specs) contains no MiOS componentry; Xbox features, start menu layouts, feature enablements,
> drivers, settings, users, etc-etc(!!!)

Standing rules that apply (operator): the SSOT always pulls latest upstream (no pins; record
resolved versions only in build output); credentials and every other value come from the resolved
layered SSOT (vendor -> host -> user) at build, install and runtime, natively in DISM, UUP Dump,
answer files and autounattend, for all MiOS images; everything is fixed in MiOS code, never only on
the live host. A command finishing is not completion: each condition needs a positive control and a
negative control (a planted defect makes the check fail).

## Stopping conditions

| ID | Condition | Evidence that closes it |
|----|-----------|-------------------------|
| XB-1 | **One spec.** The MiOS-Xbox specification (start menu and taskbar layout, Xbox features, feature enablements, drivers, guest tools, settings, users, MiOS componentry) lives in the SSOT and is the only input; legacy hardcoded values (e.g. left-aligned taskbar/start) are gone from every pipeline. | Spec-conformance gate passes on the rendered pipeline inputs; planting the legacy left-aligned value (or any off-spec literal) makes it fail |
| XB-2 | **Builds new, latest.** A fresh image is built end to end (UUP Dump -> DISM servicing -> answer file/autounattend -> ISO/disk) from latest upstream Windows and latest drivers/tools, with resolved versions recorded only in build output. | Build log + manifest of resolved versions; a pinned version planted in the SSOT is rejected by the pins gate |
| XB-3 | **Everything in the image, GPU drivers by detection.** Virtualization drivers (virtio-win), guest tools (QEMU guest agent, SPICE tools), the Looking Glass IDD, Xbox features, MiOS componentry and settings are serviced into the image natively (DISM/answer file), not applied by hand afterwards. **No GPU vendor driver (NVIDIA, Intel, AMD) is hardcoded or baked into the image:** a MiOS post-setup step in the guest detects the GPU hardware present (PCI vendor/device IDs) and installs the matching driver from the vendor's upstream, latest, with signature/integrity verification, recording the resolved version. | Offline image inspection lists every spec item and contains no GPU vendor driver; in the guest the post-setup step installs the driver matching the detected GPU (positive) and installs nothing for an absent vendor (negative); planting a vendor driver package in the image spec fails the gate |
| XB-4 | **Users from SSOT.** Accounts, passwords and autologon in the image equal the resolved layered SSOT values. | Rendered answer file + in-guest check equal the SSOT; an empty SSOT credential fails the build |
| XB-5 | **Launches on the MiOS host.** A new VM boots the new image on this host with SPICE + VNC consoles, Looking Glass, the dGPU passthrough, display mirroring, and guest tools active. | virsh/QEMU guest agent + WinRM checks green; Looking Glass and VNC show the desktop |
| XB-6 | **In-guest conformance.** A WinRM conformance probe verifies every spec item live in the booted guest (taskbar alignment, start layout, Xbox features, drivers, MiOS components, users). | Probe all green on the new VM; the same probe reports the legacy VM (mios-xbox-current) as non-conforming |
| XB-7 | **In the codebase, gated, merged.** All pipeline code is MiOS code, gates green, committed, PR merged to main. | PR URL, green gates |

## Non-goals
- Changing the host display layout (HDMI-A-1 1920x1080@144 stays), Hyprland/Quickshell, or the RDP stack.
- Deleting or redefining the current VMs (mios-xbox-current, mios-xbox-baseline) before the new VM passes XB-5/XB-6 and the operator approves retiring them.
- Pinning any upstream version; hardcoding any credential; hardcoding or pre-baking any GPU vendor driver (NVIDIA/Intel/AMD) -- detect and install from upstream post-setup.
- Running two VMs on the RTX 4090 at once (it needs a cold stop/start to recover).

## Operator decisions (2026-10-16)
- **Accounts:** separate desktop admin account (name from SSOT) with persistent autologon; the built-in RID-500 Administrator is disabled at setup finalize.
- **Xbox features:** ship the full-screen experience (Xbox Mode) pair plus all feature IDs in mios-xbox-features.txt (moved into the SSOT), and research upstream (web) for more current/future Windows features that complement MiOS systems; add them as named SSOT toggles.
- **Store-only components:** bake what can be baked offline (WebView2 evergreen offline installer, latest); Gaming Services via a MiOS first-logon hydrate step declared in the SSOT and verified by the probe.
- **DISM servicing:** GPU-less build worker VM (mios-xbox-worker) on a copy-on-write overlay of the current disk; the original disk/domain stay untouched; Linux tools only for offline inspection.
- **Defaults taken from the plan:** taskbar extras TaskbarDa=0, TaskbarMn=0, ShowTaskViewButton=1 (in SSOT), emoji/touch via TipbandDesiredVisibility=1; Game Mode on, Game Bar on with background capture off, Auto HDR on; Hyper-V off, WSL/VirtualMachinePlatform only with nested virtualization, NetFx3 off, VirtIO-FS + WinFsp on; drop posture, debloat_profile selects [windows.xbox.debloat]; components per plan Q8; new VM mios-xbox, 128G thin qcow2; start2.bin/VisiblePlaces captured per build family on the worker.
- **GPU drivers:** never in the image; detected and installed from upstream post-setup (XB-3).
