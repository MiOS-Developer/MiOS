<!-- AI-hint: Human-readable reference documentation for the MiOS RPM package ecosystem -- the irreducible host substrate beneath the bootc/OCI image. Agents should use mios.toml as the source of truth for package selection while using this file to understand the rationale and the project-wide delivery policy (RPM = substrate only; apps -> Flatpak, services -> Container, guests -> VM).
     AI-related: /etc/mios/mios.toml, /usr/share/mios/mios.toml, /usr/share/mios/configurator/mios.html, /usr/lib/mios/env.d/flatpaks.env, /usr/libexec/mios/intel-cdi-specs-generator, /usr/libexec/mios/mios-cdi-detect, mios-cdi-detect, mios-bootstrap, mios-flatpak-install, mios-no-audit -->
# 'MiOS' -- Package Documentation
> **Attribution:** MiOS-DEV (Administrative Alias)
> **Infrastructure:** 'MiOS' Open-Source Build Pipeline
> **License:** Apache-2.0 (Open-Source Infrastructure)

---

## Purpose -- where RPMs sit in the whole system

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the entire OS is a single container image -- boot it,
`bootc upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is
*also* a **local, self-replicating, agentic AI operating system** (local LLM
inference lanes, an OpenAI-compatible front door, a multi-agent orchestration
pipeline, and a PostgreSQL+pgvector memory, all on your own hardware).

This document describes the **bottom layer** of that system: the RPM packages
baked into the image at build time. They are the *irreducible host substrate* --
the kernel, drivers, runtimes, daemons, and admin tooling that everything else
stands on. The GPU stack here is what lets the AI inference lanes and the
passthrough VMs each claim hardware via CDI; the container runtime here is what
runs the agent Quadlets; the security daemons here are what keep the agent plane
sandboxed. Without this substrate there is no image to ship, no bootc lifecycle
to carry it forward, and no place for the agent stack to live.

So the RPM surface is deliberately *minimal*: it exists only to make the three
higher delivery formats (Flatpak / Container / VM) function. Applications, AI
services, and guest workloads are **not** RPMs here -- see the Delivery Policy
below. This file is the human-readable rationale for *why* each substrate
package is present; the machine-readable source of truth is `mios.toml`.

---

## SSOT is mios.toml (PACKAGES.md is documentation only)

**This file is documentation, not the source of truth.** As of MiOS
v0.3.0 (2026-07-18) the legacy fenced ```packages-<section>```
fallback has been **removed from the runtime path entirely**. The
definitive package surface lives in `mios.toml [packages.<section>].pkgs`,
resolved through the layered overlay chain (highest precedence first):

```
$MIOS_TOML override                build-time staging only
~/.config/mios/mios.toml           per-user override
/etc/mios/mios.toml                host/admin override
/ctx/mios-bootstrap/mios.toml      bootstrap-side override (build-time)
/usr/share/mios/mios.toml          vendor defaults
/ctx/usr/share/mios/mios.toml      build context (during OCI build)
```

To add, remove, or replace packages, edit the matching
`[packages.<section>]` table in any layer. The bootstrap entry
(`mios-bootstrap/mios.toml`) is the canonical user-edit copy --
operators can ship a single-file deployment override without
touching mios.git. The HTML configurator at
`/usr/share/mios/configurator/mios.html` is the operator-facing
editor for the same TOML.

`automation/lib/packages.sh` reads exclusively from the TOML chain.
This file remains under `usr/share/doc/mios/reference/PACKAGES.md`
as a human-readable reference for *why* each package is in MiOS --
the prose between sections is the documentation; the fenced lists
are the quickly-reviewable summary. Keep both in sync as a courtesy
to readers, but only mios.toml affects what dnf actually installs.

---

## DELIVERY POLICY (project-wide invariant)

Every software artifact in MiOS ships as **one of three formats**, and the
choice of format is what keeps the immutable-image promise intact: apps stay
sandboxed and user-replaceable, services stay isolated and version-locked to the
image, and guest OSes stay fully contained.

| Format | What goes here | Where it's defined |
|---|---|---|
| **Flatpak** | User-facing applications (GUI apps, games, IDEs, file managers, terminal emulators, viewers, editors, gaming clients, virt GUIs). | `mios-bootstrap/mios.toml` `[desktop].flatpaks` → `MIOS_DESKTOP_FLATPAKS` build-arg → `/usr/lib/mios/env.d/flatpaks.env` → `mios-flatpak-install` at first boot. |
| **Container** (Quadlet / Podman / Distrobox) | Long-lived services and isolated workloads (the local AI plane -- `mios-llm-light`, the gated `mios-llm-heavy`/`mios-llm-heavy-alt` lanes, `mios-pgvector`, `mios-open-webui`, `mios-searxng`; plus Forgejo, Ceph daemons, k3s workloads, NUT). | `etc/containers/systemd/*.container` (Quadlet) or `usr/share/distrobox/`. |
| **VM** (libvirt / QEMU) | Heavyweight guest workloads needing a full guest OS (Windows guests, legacy distros, hardware-emulation testbeds). | Driven by libvirt/QEMU from the host substrate. |

**RPM (this file)** is reserved strictly for the **irreducible host
substrate** — the minimum set required for the three delivery formats
above to function. That means: kernel + drivers + firmware, boot
tooling, init / PID-1, system libraries, system daemons, the runtimes
themselves (Podman/bootc, Flatpak/portals, libvirt/QEMU), display-
server foundation, host-scope security daemons, filesystem drivers,
core CLI admin tools, and image-build toolchain.

**Before adding any RPM to this file:** ask "is this an app, a service,
or a guest workload?" If yes to any, it does NOT belong here — route
it to the appropriate Flatpak / Container / VM channel above. Repos
in `automation/05-enable-external-repos.sh` follow the same rule:
no application RPM repos (the VSCodium repo was removed for this
reason).

**SECUREBOOT NOTE:** Proprietary NVIDIA drivers are included as
essential RPM artifacts for Microsoft-compliant SecureBoot verification.
Steam ships exclusively as a Flatpak (`com.valvesoftware.Steam`).

---

# 'MiOS' v0.3.0 -- Package Manifest

This file is the human-readable rationale for the RPM substrate; the
machine-readable **single source of truth** is `mios.toml`
`[packages.<section>].pkgs`, parsed by `automation/lib/packages.sh`.
To add a package, add it to the appropriate `[packages.<section>]` table
in `mios.toml`. Apps go in `mios.toml` `[desktop].flatpaks`; long-lived
services go in `etc/containers/systemd/`.

(The fenced ```packages-<section>``` blocks below mirror the TOML sections for
quick review. Since v0.3.0 they are documentation only and no longer parsed at
build time.)

**CHANGELOG v0.2.0:**
- Standardized versioning across the entire stack.
- Added uupd (unified updater replacing bootc-fetch-apply-updates.timer)
- Added greenboot + greenboot-default-health-checks (auto-rollback on boot failure)
- Added cosign (signed image verification for `bootc switch --enforce-container-sigpolicy`)
- Added toolbox (companion to existing distrobox)
- Added kubectl, helm (client tooling; k3s binary still from rancher install)
- Added podman-plugins, podman-docker, containers-common
- Added nvidia-container-selinux (missing SELinux policy for toolkit on bootc)
- Added steam-devices (udev rules for controllers)
- Added aide, openscap-scanner, scap-security-guide, libpwquality, setools-console, nftables (security)
- Added cloud-init, wslu, python3-pip, libei (Podman-machine + WSL + desktop input)
- Added freerdp, freerdp-libs, virt-viewer, qemu-device-display-virtio-gpu (remote/display)
- NEW SECTION: packages-updater (uupd + greenboot) wired into 43-uupd-installer.sh
- Fix: Containerfile removed external akmods FROM stages (ucore-hci bakes NVIDIA in)
- Fix: scripts/41-47 no longer duplicate dnf installs - PACKAGES.md is now the
       sole source of truth; 40-series scripts handle config+services only
- Added missing audited tools: strace, lsof, iotop, ntfs-3g, efibootmgr, nm-connection-editor

**CHANGELOG v0.2.0 (previous):**
- Added bootupd (unified bootloader updates -- Fedora 44 phase 1)
- Added dnf5-plugins (versionlock support for critical package pinning)
- Added systemd-boot-unsigned (UKI preparation -- future composefs+UKI chain)
- Added libsss_nss_idmap (fixes sssd-related dep resolution on F44)
- Added tpm2-tools (TPM2 support for measured boot / future attestation)
- Added clevis, clevis-luks (automated LUKS unlock via TPM2/Tang)
- Moved driverctl from security to utils (better categorization)

**CHANGELOG v0.2.0 (previous):**
- Removed htop (use btop instead)
- Added nvidia-settings to NVIDIA section
- Added avahi/nss-mdns for .local network discovery
- Added network-discovery package section

---

## Repositories

RPMFusion Free + Nonfree for NVIDIA drivers and multimedia codecs.
CrowdSec official repo with Fedora 40 fallback for Rawhide compatibility.

```packages-repos
rpmfusion-free-release-rawhide
rpmfusion-nonfree-release-rawhide
fedora-workstation-repositories
dnf-plugins-core
dnf5-plugins
```

## Base -- Security Stack (installed pre-pipeline)

Installed by Containerfile before automation/build.sh runs because every
later phase assumes SELinux tooling, audit, fapolicyd, crowdsec, and
usbguard are already present. Strict install: a missing package here is a
build failure, not a silent skip.

```packages-base
policycoreutils-python-utils
selinux-policy-targeted
firewalld
audit
fapolicyd
crowdsec
usbguard
# D-Bus: dbus-broker (CoreOS default) is the system bus on every
# deployment shape, including WSL. The WSL audit-subsystem-stripped
# kernel is handled by usr/lib/systemd/system/dbus-broker.service.d/
# 10-mios-no-audit.conf, which drops `--audit` from the broker's
# ExecStart. The legacy /usr/bin/dbus-daemon package is intentionally
# NOT installed -- shipping both broker and reference daemon would
# create a unit-file conflict (alias dbus.service -> two providers).
```

## Moby -- Docker-compatible engine

Installed by automation/21-moby-engine.sh.

```packages-moby
moby-engine
```

## UKI -- Unified Kernel Image tooling

Installed by automation/23-uki-render.sh.

```packages-uki
systemd-ukify
```

## SBOM -- Software Bill of Materials tooling

Installed by automation/90-generate-sbom.sh.

```packages-sbom-tools
syft
```

## K3s SELinux build dependencies

Installed by automation/37-k3s-selinux.sh to build the k3s SELinux module.

```packages-k3s-selinux-build
selinux-policy-devel
git
make
```

## Kernel

Kernel extras + development headers for akmod-nvidia and DKMS builds.
Base kernel ships with fedora-bootc:rawhide -- NEVER upgrade it in-container.
Upgrading triggers dracut under tmpfs which breaks the initramfs.

```packages-kernel
kernel-modules-extra
kernel-devel
kernel-headers
kernel-tools
glibc-headers
glibc-devel
python3
```

## GNOME 50 Desktop

MINIMAL GNOME shell -- infrastructure ONLY. **PROJECT INVARIANT:**
applications ship as Flatpaks (see `mios.toml` `[desktop].flatpaks`),
only system dependencies ship as RPMs. No exceptions.

GNOME 49+: systemd is a HARD dependency. gnome-session's built-in service
manager was removed. Full systemd user session support is required.
GNOME 50: X11 session removed upstream. Wayland-only (Fedora 43+ dropped X11).

```packages-gnome
# ── Core shell (auto-pulls: mutter, gjs, gtk4, libadwaita, gnome-desktop4,
#    gnome-session, gnome-settings-daemon, gsettings-desktop-schemas, colord,
#    dconf, adwaita-icon-theme, adwaita-cursor-theme, pipewire, polkit) ──
gnome-shell
gnome-session-wayland-session
gnome-control-center
gnome-keyring
gdm
# ── System services (NOT apps) ──
# gnome-remote-desktop is a systemd-managed RDP/VNC service, not a user app.
gnome-remote-desktop
# Theme/wallpaper data, not an executable app.
gnome-backgrounds
# ── Application RPMs removed per project invariant ──
# ptyxis           -> Flathub: org.gnome.Ptyxis
# nautilus         -> Flathub: org.gnome.Nautilus
# gnome-software   -> Flathub: org.gnome.Software (native GNOME, already in mios.toml)
# ── Extensions ──
gnome-shell-extension-appindicator
gnome-shell-extension-dash-to-dock
# ── Portals ──
xdg-user-dirs
xdg-utils
xdg-desktop-portal
xdg-desktop-portal-gnome
xdg-desktop-portal-gtk
# ── Audio ──
pipewire-alsa
pipewire-pulseaudio
wireplumber
# ── GStreamer (MUST be explicit -- ucore fc43 base ships older GStreamer that
#    is ABI-incompatible with GNOME 50. Without these, gnome-shell crashes on
#    launch with "undefined symbol: gst_state_get_name" in libgstplay) ──
gstreamer1
gstreamer1-plugins-base
gstreamer1-plugins-good
# ── Hardware ──
upower
gnome-bluetooth
bluez
bluez-tools
# ── Flatpak (gnome-software manages these -- no rpm-ostree plugin needed) ──
flatpak
# ── Filesystem access ──
gvfs
gvfs-smb
gvfs-mtp
# ── Networking ──
NetworkManager-wifi
NetworkManager-openvpn-gnome
nm-connection-editor
# ── Locale ──
glibc-langpack-en
# ── Qt Adwaita theming ──
qt6-qtbase-gui
qt6-qtwayland
qadwaitadecorations-qt5
adw-gtk3-theme
```

## GNOME Flatpak Runtime -- portals + audio + theming for Flatpaks via WSLg

The MiOS podman backend (Windows-side) does not host its own
GNOME session -- WSLg is the Windows compositor and operators see
Flatpaks (Ptyxis terminal, Nautilus file manager, Software app store,
Epiphany browser, Flatseal permissions UI) as Windows windows routed
through the WSLg portal. Those Flatpaks ship their own GTK/libadwaita
via `org.gnome.Platform`, but they still need a **host portal +
audio router + Qt-Adwaita theming** so file dialogs, drag-and-drop,
audio output, and consistent theming all work. This section is the
**runtime-only** subset of `packages-gnome` -- the bits required for
Flatpaks to function on the dev VM, with **no display manager, no
gnome-shell, no GDM, no session services**.

Deployed bare-metal / Hyper-V / QEMU MiOS hosts install
`packages-gnome` (full session) instead.

```packages-gnome-flatpak-runtime
# ── Portals: file dialogs / drag-and-drop / desktop integration ──
xdg-user-dirs
xdg-utils
xdg-desktop-portal
xdg-desktop-portal-gnome
xdg-desktop-portal-gtk
# ── Audio: PipeWire is the host router; Flatpaks bind to it via
#    portal sockets ──
pipewire-alsa
pipewire-pulseaudio
wireplumber
# ── GStreamer base: required by libsoup / libgweather inside
#    Flatpak runtimes for media-rendering helpers ──
gstreamer1
gstreamer1-plugins-base
gstreamer1-plugins-good
# ── GVFS: lets Nautilus / Software / Ptyxis traverse SMB / MTP /
#    other backend filesystems via the host ──
gvfs
gvfs-smb
gvfs-mtp
# ── Locale ──
glibc-langpack-en
# ── Qt Adwaita theming -- so Qt apps (e.g. Wireshark Flatpak)
#    match GNOME look on WSLg ──
qt6-qtbase-gui
qt6-qtwayland
qadwaitadecorations-qt5
adw-gtk3-theme
# ── Flatpak itself; org.gnome.Software (Flathub) handles install/upgrade ──
flatpak
```

## GNOME Core Apps -- DO NOT INSTALL AS RPM

**PROJECT INVARIANT:** GNOME Core apps are user-facing applications and
ship STRICTLY as Flatpaks. Add the Flathub ref to `mios.toml`
`[desktop].flatpaks` instead of uncommenting an RPM here.

| RPM (do not use) | Flathub ref |
|---|---|
| papers              | org.gnome.Papers |
| loupe               | org.gnome.Loupe |
| showtime            | org.gnome.Showtime |
| gnome-text-editor   | org.gnome.TextEditor |
| gnome-disk-utility  | org.gnome.DiskUtility |
| gnome-system-monitor| (not on Flathub; use `btop`/`nvtop` CLI) |
| baobab              | org.gnome.baobab |
| gnome-connections   | org.gnome.Connections |
| gnome-tweaks        | (use Flathub: com.mattjakeman.ExtensionManager) |
| file-roller         | org.gnome.FileRoller |
| gnome-calculator    | org.gnome.Calculator |
| gnome-calendar      | org.gnome.Calendar |
| gnome-contacts      | org.gnome.Contacts |
| gnome-clocks        | org.gnome.clocks |
| gnome-weather       | org.gnome.Weather |
| gnome-maps          | org.gnome.Maps |
| gnome-characters    | org.gnome.Characters |
| gnome-font-viewer   | org.gnome.FontManager |
| gnome-music         | org.gnome.Music |
| snapshot            | org.gnome.Snapshot |
| decibels            | org.gnome.Decibels |
| cheese              | org.gnome.Cheese |
| gnome-logs          | org.gnome.Logs |
| deja-dup            | org.gnome.DejaDup |
| simple-scan         | org.gnome.SimpleScan |
| seahorse            | org.gnome.seahorse.Application |
| gnome-boxes         | org.gnome.Boxes |

```packages-gnome-core-apps
# Intentionally empty. See table above; install via mios.toml flatpaks.
```

## GPU Drivers -- Mesa (AMD / Intel / software fallback)

Universal Mesa stack supporting all AMD and Intel GPUs out of the box.
Mesa 26: ACO is now default shader compiler for RadeonSI.

```packages-gpu-mesa
mesa-vulkan-drivers
mesa-dri-drivers
mesa-va-drivers
vulkan-loader
vulkan-tools
libva-utils
linux-firmware
# microcode_ctl removed: redundant on F44+
```

## GPU Drivers -- AMD Compute (optional, fault-tolerant)

ROCm OpenCL/HIP for AMD compute workloads. This is one of the three GPU
compute paths (AMD / Intel / NVIDIA) that the CDI generators below expose to
containers -- the same hardware the local inference lanes claim for generation.

```packages-gpu-amd-compute
rocm-opencl
rocm-hip
rocm-runtime
rocm-smi
rocminfo
```

## GPU Drivers -- Intel Compute (oneAPI Level Zero)

Intel GPU compute runtime for OpenCL and Level Zero API.
Supports Intel Arc, Iris Xe, and integrated GPUs.
All packages are in official Fedora repos -- no extra repo needed.

```packages-gpu-intel-compute
intel-compute-runtime
intel-media-driver
# Intel oneAPI Level Zero loader. Ships in F44 via the
# intel-compute-runtime source RPM under the name intel-level-zero
# (the generic standalone "level-zero" loader is still in active
# Fedora packaging review -- track Phoronix/Fedora-IntelCompute2025).
intel-level-zero
# IGT (Intel GPU Tools): renamed from intel-gpu-tools to igt-gpu-tools
# in Fedora 44; provides intel_gpu_top, IGT test runner, and pmu-help
# for diagnostics. The "needs libproc2.so.0" issue cleared on F44.
igt-gpu-tools
```

## GPU Drivers -- NVIDIA (akmod, builds for any NVIDIA card)

NVIDIA proprietary drivers via RPMFusion akmod. Builds kmod at image time.
Driver 590+: Open kernel modules are DEFAULT for Turing (RTX 20+) and newer.
Blackwell (RTX 50): Open modules are the ONLY option -- proprietary incompatible.
WARNING: RTX 50-series has a VFIO reset bug -- see /usr/share/doc/mios-vfio-warning.txt
CDI is now the default mode in nvidia-container-toolkit v1.19+.

```packages-gpu-nvidia
akmod-nvidia
xorg-x11-drv-nvidia-cuda
nvidia-container-toolkit
nvidia-persistenced
nvidia-settings
xorg-x11-drv-nvidia-power
# v2.2 additions
nvidia-container-selinux
```

## GPU Container Device Interface toolkits (vendor CDI generators)

Out-of-Fedora binaries that emit `/run/cdi/*.{yaml,json}` so podman
containers can claim GPU access via `--device <vendor>.com/gpu=all`.
This CDI layer is the bridge between the GPU driver RPMs above and the
container plane: it is how the inference-lane Quadlets (`mios-llm-light` and
the gated heavy lanes) and the VFIO-passthrough VMs each get hardware.
NVIDIA's `nvidia-ctk` is part of `nvidia-container-toolkit` (above);
the AMD + Intel paths install via `automation/41-gpu-cdi-toolkits.sh`
because neither ships in Fedora repos as of May 2026:

* `amd-ctk` -- AMD Container Toolkit v1.3+ (RHEL9 RPM works on F44).
  Source: github.com/ROCm/container-toolkit. Generates
  `/run/cdi/amd.json` for any host with `/dev/kfd`. CDI key:
  `amd.com/gpu=all`.

* `intel-cdi-specs-generator` -- intel/intel-resource-drivers-for-
  kubernetes static binary. Best-effort (v0.x upstream). Installed
  to `/usr/libexec/mios/intel-cdi-specs-generator`. Generates
  `/run/cdi/intel.yaml` for hosts with an Intel render node
  (vendor:0x8086 on /dev/dri/renderD*). CDI key: `intel.com/gpu=all`.

Both are consumed by `/usr/libexec/mios/mios-cdi-detect` at boot
(via `mios-cdi-detect.service`). Missing toolkits make the
corresponding branch a no-op rather than failing the boot.

```packages-gpu-cdi-toolkits
# Intentionally empty -- amd-ctk + intel-cdi-specs-generator are
# fetched by automation/41-gpu-cdi-toolkits.sh from upstream
# GitHub releases (same pattern as 37-aichat.sh / 38-oh-my-posh.sh).
# Versions tracked via record_version (build metadata).
```

## Virtualization -- KVM / QEMU / Libvirt

System-level KVM stack: hypervisor, libvirt daemon, firmware, CLI helpers.
This is the substrate for the **VM** delivery channel and for VFIO-PCI GPU
passthrough (hand a discrete GPU to a Windows guest and game on it).
GUI front-ends ship as Flatpaks per project invariant -- see `mios.toml`
`[desktop].flatpaks` (`org.virt_manager.Manager`,
`org.remmina.Remmina` for VNC/SPICE/RDP viewer needs).

```packages-virt
qemu-kvm
libvirt
libvirt-daemon
virt-install
edk2-ovmf
swtpm
swtpm-tools
dnsmasq
mdevctl
libguestfs-tools
# v2.2 additions
virt-v2v
qemu-device-display-virtio-gpu
virt-firmware
python3-cryptography
# ── Application RPMs removed per project invariant ──
# virt-manager  -> Flathub: org.virt_manager.Manager
# virt-viewer   -> Flathub: org.remmina.Remmina (covers SPICE/VNC/RDP)
```

## Container Runtime

Podman, Buildah, Skopeo, bootc tooling, and OCI image building. This is the
substrate for the **Container** delivery channel -- the engine that runs every
agent/service Quadlet (the AI lanes, `mios-pgvector`, `mios-open-webui`,
`mios-searxng`, Forgejo, Ceph, k3s) -- and the toolchain that builds and
upgrades the MiOS image itself via `bootc`.

```packages-containers
podman
podman-compose
buildah
skopeo
bootc
osbuild
osbuild-composer
osbuild-selinux
composer-cli
rpm-ostree
crun
netavark
aardvark-dns
slirp4netns
composefs
container-selinux
qemu-img
image-builder
bootc-image-builder
dracut-live
squashfs-tools
containers-common
toolbox
kubectl
helm
podman-plugins
cosign
# Build toolchain is composed from [packages.build-toolchain].
# Default self-build retention keeps it in the deployed image.
# REMOVED -- podman-docker: conflicts with moby-engine from ucore-hci base
```

## Build Toolchain and retained development dependencies

These compilers and devel headers are required by image-build phases
(`37-k3s-selinux.sh` builds the k3s SELinux policy module,
`53-bake-lookingglass-client.sh` compiles Looking Glass B7 from source)
and by a MiOS host developing and rebuilding its next generation.

`[packages.self-build]` composes this block using `requires_sections`.
The package resolver deduplicates the resulting closure. Installation must
cover the chosen profile independently of whether `automation/21-virt.sh`
runs. `automation/91-strip-build-toolchain.sh` reads the SSOT retention
policy; the default `retain_toolchain = true` preserves development tools.
Set it to false only when deliberately building a stripped deployment.

```packages-build-toolchain
make
gcc
gcc-c++
cmake
golang
selinux-policy-devel
binutils
pkgconf-pkg-config
```

## Self-Building Tools

Tools needed for the image to rebuild itself -- part of what makes MiOS
"self-replicating": a booted host can build the next image of itself.
The authoritative list is `[packages.self-build].pkgs`, with compiler and
linker dependencies inherited from `[packages.build-toolchain]`. It covers
OCI image construction and transport, rootless container prerequisites,
source checkout and archives, and repository verification. The development
container composes these groups rather than maintaining a separate compiler
list. Requirements for shared Python services remain in their consumed
requirements file, and direct system Python dependencies remain RPMs.

Artifact builders and containerized services use their configured OCI images;
they do not require redundant host installations of the same server stack.
Package resolution must report missing required groups and cycles before
installation. Verify actual image builds and service startup in MiOS-DEV;
package declarations alone cannot establish deployed self-build capability.

## Boot & Update Management

Bootloader updates and system update tooling for bootc systems.
bootupd: unified bootloader update service (Fedora 44 phase 1).
dnf5-plugins: versionlock for pinning critical packages (Mesa, PipeWire, etc.)

```packages-boot
bootupd
dnf5-plugins
systemd-boot-unsigned
efibootmgr
systemd-ukify
binutils
efitools
sbsigntools
tpm2-tss
```

## Cockpit Web Management

Full Cockpit ecosystem -- file browser, virt, podman, storage, network,
SELinux, ostree, kdump, sosreport -- plus the Performance Co-Pilot
(PCP) stack that powers the Metrics page.

`cockpit-packagekit` is intentionally **excluded**: PackageKit is in
`packages-bloat` because bootc + Flatpak handle every update path on
MiOS, so the Cockpit "Software Updates" tab has nothing to drive.

`pcp-zeroconf` is the load-bearing fix for the "Metrics history could
not be loaded -- pmlogger.service is not running" error: stock `pcp`
ships pmlogger.service / pmproxy.service with `disabled` preset, and
`pcp-zeroconf` is the package whose `%post` flips the preset and
writes the default archive-collection config under
`/etc/pcp/pmlogger/control.d/`. Without zeroconf the Cockpit Metrics
page renders an empty error state on first boot.

`cockpit-pcp` is restored (was removed in v0.2.0 with the rationale
"now native in cockpit-bridge since Cockpit 326" -- accurate for the
LIVE metrics view, but the historical-archive renderer in the Metrics
tab still consults the cockpit-pcp bridge for `/var/log/pcp/pmlogger/`
archives).

PMDAs added so the Metrics tab has interesting series to plot:

* `pcp-pmda-systemd` -- per-service CPU / mem / I/O metrics, indexed
  by unit name. Required for the "by service" Metrics drill-down.
* `pcp-pmda-openmetrics` -- scrapes any Prometheus `/metrics` endpoint
  on the host into PCP, so node_exporter / cadvisor / etc. show up
  in the same Cockpit Metrics graphs as native PCP metrics.

```packages-cockpit
cockpit
cockpit-system
cockpit-ws
cockpit-bridge
cockpit-storaged
cockpit-networkmanager
cockpit-podman
cockpit-machines
cockpit-ostree
cockpit-selinux
cockpit-files
cockpit-pcp
cockpit-sosreport
cockpit-kdump
pcp
pcp-system-tools
pcp-zeroconf
pcp-pmda-systemd
pcp-pmda-openmetrics
```

## Windows Interop & Remote Desktop

Tools for Hyper-V Enhanced Session, SMB, and native RDP.

```packages-wintools
hyperv-tools
samba
samba-client
cifs-utils
# freerdp-libs stays as a system library (gnome-remote-desktop and other
# host services link against it). The freerdp CLI client itself was an
# application -- removed per project invariant; use org.remmina.Remmina
# (already in mios.toml [desktop].flatpaks) for RDP/SPICE/VNC.
freerdp-libs
```

## Security

Host-based IPS, application whitelisting, USB device control. This is the
substrate behind Architectural Law 6 (unprivileged Quadlets): SELinux,
fapolicyd deny-by-default, USBGuard, and CrowdSec are what keep the agent
plane sandboxed and least-privileged.
CRITICAL: nvidia-container-toolkit >= v1.17.8 required (CVE-2025-23266/23267).

```packages-security
crowdsec
crowdsec-firewall-bouncer-nftables
firewalld
fapolicyd
fapolicyd-selinux
usbguard
setroubleshoot-server
policycoreutils-python-utils
audit
tpm2-tools
clevis
clevis-luks
# v2.2 additions
aide
openscap-scanner
scap-security-guide
libpwquality
nftables
policycoreutils
setools-console
cosign
# v2.3 additions
# iptables-legacy: WSL2 kernel does NOT support nftables. Without this package
#   iptables-nft fails and Podman networking breaks inside WSL. Safe on bare
#   metal - coexists with nftables, only invoked when WSLInterop is detected.
iptables-legacy
```

## Gaming

Gaming clients and Wine ship STRICTLY as Flatpaks per project invariant.
This section retains only the system-level pieces that Flatpak gaming
clients depend on (gamemode daemon, vulkan tooling, controller udev
rules, the gamemode shell extension).

NTSYNC kernel module available in Fedora 44 for improved Wine/Steam
performance via the Steam Flatpak's bundled Proton.

```packages-gaming
# ── System daemons / shell extension / udev (NOT apps) ──
gamemode
gnome-shell-extension-gamemode
vulkan-tools
steam-devices
# ── Application RPMs removed per project invariant ──
# steam            -> Flathub: com.valvesoftware.Steam (bundles Proton)
# lutris           -> Flathub: net.lutris.Lutris
# dosbox-staging   -> Flathub: io.github.dosbox_staging.dosbox-staging
# protontricks     -> Flathub: com.github.Matoking.protontricks
# mangohud         -> Flathub extension: org.freedesktop.Platform.VulkanLayer.MangoHud
# gamescope        -> shipped inside Steam Flatpak; install separately only if
#                     a non-Steam Wayland gaming session is required (then via
#                     Flathub: org.gamescope.Session if/when published)
# wine, wine-mono, wine-dxvk, winetricks
#                  -> non-Steam Windows apps: Flathub com.usebottles.bottles
#                     (bundles its own Wine/DXVK/winetricks)
```

## Guest Agents

Hypervisor integration services for VMs.

```packages-guests
qemu-guest-agent
hyperv-daemons
open-vm-tools
spice-vdagent
spice-webdavd
libvirt-nss
```

## Storage

Distributed/shared storage, multipath, iSCSI.

```packages-storage
nfs-utils
rpcbind
glusterfs
glusterfs-fuse
glusterfs-server
ceph-common
iscsi-initiator-utils
targetcli
device-mapper-multipath
sg3_utils
lvm2
stratis-cli
stratisd
xfsprogs
btrfs-progs
e2fsprogs
mdadm
ntfs-3g
```

## Ceph Distributed Storage

Cephadm orchestrator + CephFS kernel client for native distributed storage.
All Ceph server daemons (MON/OSD/MGR/MDS) run as Podman containers via cephadm.
This is part of the "grow the box into a one-node cluster in place" path that
the immutable image enables without re-imaging.

```packages-ceph
ceph-common
cephadm
ceph-fuse
ceph-selinux
```

## K3s Lightweight Kubernetes

K3s binary is downloaded directly (not via dnf).
k3s-selinux only exists for RHEL/CentOS -- not available on Fedora Rawhide.

```packages-k3s
container-selinux
```

## High Availability -- Pacemaker / Corosync

Full HA cluster stack with fence agents and SBD.

```packages-ha
pacemaker
corosync
pcs
fence-agents-all
fence-virt
resource-agents
sbd
booth
booth-core
booth-test
dlm
corosync-qdevice
corosync-qnetd
libqb
libibverbs
```

## CLI Utilities

Common command-line tools and system utilities.

```packages-utils
git
tmux
vim-enhanced
wget2-wget
curl
btop
nvtop
fastfetch
lm_sensors
smartmontools
tuned
tuned-ppd
fuse
fuse3
7zip-standalone
unzip
zstd
rsync
tree
jq
yq
bc
patch
openssl
distrobox
just
driverctl
tmt
ansible-core
# v2.2 additions
wslu
python3-pip
# /usr/bin/mios (Python CLI) imports the 'requests' module. Without
# this package the import fails at runtime and a pip install isn't
# viable on the read-only composefs /usr surface.
python3-requests
cloud-init
libei
strace
lsof
iotop
# v2.4 Universal Para-virt additions
socat
syft
oras-cli
```

## Android -- Waydroid

Waydroid container runtime for Android apps.
Note: NVIDIA GPUs lack full 3D acceleration in Waydroid (Mesa/AMD/Intel only).

```packages-android
waydroid
```

## Looking Glass B7 -- Build Dependencies

These packages are installed during the build to compile Looking Glass B7.
They are REMOVED after compilation to keep the image small. Looking Glass is
the low-latency frame relay that pairs with VFIO GPU passthrough so the host
sees a passthrough VM's display without a second monitor.

```packages-looking-glass-build
cmake
gcc
gcc-c++
make
binutils
pkgconf-pkg-config
libglvnd-devel
fontconfig
fontconfig-devel
spice-protocol
nettle-devel
gnutls-devel
libXi-devel
libXinerama-devel
libXcursor-devel
libXpresent-devel
libXScrnSaver-devel
libxkbcommon-x11-devel
wayland-devel
wayland-protocols-devel
libdecor-devel
pipewire-devel
libsamplerate-devel
```

## Cockpit Plugin Build Dependencies

Build dependencies for Cockpit plugins from git.

```packages-cockpit-plugins-build
npm
gettext
```

## Network Discovery -- Avahi / mDNS

mDNS/DNS-SD for automatic .local hostname discovery on LAN.
Every 'MiOS' instance advertises Cockpit and RDP services.

```packages-network-discovery
avahi
avahi-tools
nss-mdns
```

### Phosh (Mobile Session)
```packages-phosh
phosh
phoc
gnome-calls
feedbackd
```

## Updater -- uupd / Greenboot (v2.2)

uupd is the Universal Blue unified updater. It replaces `bootc-fetch-apply-updates.timer`,
flatpak update timer, and distrobox update timer with a single Go binary that
handles all three. Ships via ublue-os/packages COPR (enabled in 05-enable-external-repos.sh).

Greenboot provides health-check driven auto-rollback: 3 failed boots triggers
`bootc rollback` via grub boot_counter. Health checks live in /etc/greenboot/check/{required,wanted}.d/.
These two are the substrate for the `git pull` / `Ctrl-Z` lifecycle that
defines MiOS as an image: uupd is the `git pull`, greenboot is the safety net.

```packages-updater
uupd
greenboot
greenboot-default-health-checks
```

---

## FreeIPA & SSSD
Zero-touch enrollment and identity management.

```packages-freeipa
freeipa-client
sssd
sssd-tools
libsss_nss_idmap
```

## AI Tools
Client-side SDK + AI assistant CLIs for the local OpenAI-compatible brain.

This section is the **client** half of the AI plane: the SDK and editor/CLI
agents that *talk to* the local inference lanes. The inference engines and the
agent orchestration themselves are **not** RPMs -- they ship as Quadlet
containers (the **Container** delivery channel): the primary lane is
`mios-llm-light` (the `llama.cpp` multi-model server fronted by the upstream
`mios-llm-light` proxy image) on the **`llm_light`** port, which also serves embeddings
(`nomic-embed-text`, OpenAI-compatible `/v1/embeddings`) and the `mios-opencode`
coder model; the gated heavy lanes are `mios-llm-heavy` (vLLM, port key `vllm`) and
`mios-llm-heavy-alt` (SGLang, port key `sglang`). The unified agent datastore is **PostgreSQL +
pgvector** (`mios-pgvector`, :5432). Every agent and tool resolves the one
endpoint from `MIOS_AI_ENDPOINT` (Architectural Law 5) rather than hard-coding a
port or vendor URL.

The packages here are deliberately thin:

* `python3-openai` -- the official OpenAI Python SDK, used by `/usr/bin/mios` to
  drive the local OpenAI-API-compatible endpoint at `MIOS_AI_ENDPOINT`. The SDK
  provides the streaming + tool-call roundtrip + structured-outputs surface that
  maps onto Architectural Law 5. F44+ ships the SDK in repos as `python3-openai`;
  it is installed via dnf rather than pip so it is captured by the image SBOM.
* `nodejs` / `npm` -- the JS runtime required by the npm-installed AI assistant
  CLIs below.
* `nano` -- baseline text editor for operator config edits.

AI assistant CLIs install globally via npm (`npm_globals` in `mios.toml`
`[packages.ai]`) by `/usr/libexec/mios/install-ai-clis.sh` during the overlay
phase; the current set is `@anthropic-ai/claude-code` and `@google/gemini-cli`.
These are OpenAI-API-compatible clients that, like every other agent, target
`MIOS_AI_ENDPOINT` and thus the same local brain -- no vendor account in the loop.

```packages-ai
python3-openai
nodejs
npm
nano
```

> **Historical note (migration complete).** Earlier MiOS revisions ran inference
> and embeddings on an **Ollama** container (the `mios-ollama` / `mios-ollama-cpu`
> units, model-bake `37-ollama-prep`, Modelfiles, and the legacy `aichat` /
> `aichat-ng` musl-binary CLIs fetched by `37-aichat.sh`). That stack has been
> fully removed: inference + embeddings now run on `mios-llm-light` (port key `llm_light`), and
> the agent datastore moved from the legacy datastore/Qdrant to PostgreSQL+pgvector. Ollama
> survives only as an **upstream API-compat reference** -- the lanes speak the
> OpenAI/Ollama-compatible API so any such client connects unchanged -- not as a
> live MiOS backend.

## Internal -- Critical Validation

These packages MUST be present in the final image. Build scripts use this
section for post-build verification.

```packages-critical
gnome-shell
gdm
podman
bootc
libvirt
kernel-core
firewalld
cockpit
NetworkManager
pipewire
tuned
chrony
openssh-server
```

## Internal -- Bloat Removal

These packages are explicitly removed during the build to keep the image
lean and free of unwanted UI components.

```packages-bloat
malcontent-control
malcontent-pam
malcontent-tools
gnome-tour
gnome-initial-setup
PackageKit
PackageKit-command-not-found
```

## Network UPS Tools (NUT)

Managed via Distrobox container to decouple hardware config from immutable core.

```packages-nut
nut
nut-client
nut-xml
usbutils
```

---
### Bootc ecosystem & resources

- bootc: <https://github.com/containers/bootc>
- bootc-image-builder: <https://github.com/osbuild/bootc-image-builder>
- bootc docs: <https://bootc-dev.github.io/bootc/>
- Universal Blue (uCore base): <https://github.com/ublue-os/main>
- uupd: <https://github.com/ublue-os/uupd>
- rechunk: <https://github.com/hhd-dev/rechunk>
- cosign: <https://github.com/sigstore/cosign>
- mios-llm-light (upstream proxy image for `mios-llm-light`): <https://github.com/mostlygeek/llama-swap>
- Project repo: <https://github.com/mios-dev/mios>
- **Sole Proprietor:** MiOS-DEV
---
