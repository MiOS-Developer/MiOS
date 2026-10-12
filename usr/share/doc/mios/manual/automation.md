<!-- AI-hint: Manual pages distilled from the source comments of automation, sanitized, each passage anchored to the comment it came from. -->

# automation

### Ports are ALLOCATED from [ports.categories] (base +...

Ports are ALLOCATED from [ports.categories] (base + index*stride), not read
off the flat table -- and the allocation must honour the layered override
chain (vendor/OEM default < /etc operator < user). The shared resolver is the
only thing that does both, so prefer it; the awk fallback below can only see
the flat vendor projection and exists purely so a stripped build host without
python still produces SOMETHING rather than an empty install.env.

<!-- mios-src:d8ec2062e5d6 from automation/35-render-ports.sh:23-28 -->

### Layout differs by upstream release. Newer tags nest the...

Layout differs by upstream release. Newer tags nest the sources under
policy/<distro>/; older ones -- which is what the vendored tarball is --
keep k3s.te FLAT at the archive root with no policy/ directory at all. The
old `find policy ...` had no fallback for that and, with `set -euo pipefail`,
a missing policy/ aborted the whole phase two seconds in with a bare exit 1 --
which is what the bake logged as "[WARN] 37-k3s-selinux".

<!-- mios-src:dcb7ebbac1a4 from automation/37-k3s-selinux.sh:49-54 -->

### The 42 MB asset we actually ship. It is a SOURCE SNAPSHOT...

The 42 MB asset we actually ship. It is a SOURCE SNAPSHOT (pyproject.toml
+ setup.py under a hermes-agent-main/ root), not a wheelhouse -- it holds
zero .whl files. Nothing consumed it: none of the probes above name it,
and pip's --find-links ignores it because an sdist filename must carry a
version (hermes_agent-<ver>.tar.gz) for the finder to parse a candidate.
So every "offline" build silently fell through to the git clone below.

<!-- mios-src:540cc82d5b8d from automation/72-hermes-agent.sh:69-74 -->

### Pre-flight

Pre-flight: the three inputs must exist. Missing inputs is a hard error
(the lint cannot make any assertion) -- but stay degrade-friendly: if the
Quadlet dir is simply absent (e.g. a minimal checkout), PASS vacuously.

<!-- mios-src:3f7fe6a1077a from automation/97-ssot-lint.sh:57-59 -->

### (1) Collect every ${MIOS_*} referenced in an...

--- (1) Collect every ${MIOS_*} referenced in an Exec=/Environment= line. ----
We scan recursively (the dir has a users/ subtree). Match the directive at
line start (Exec=, ExecStart=, ExecStartPre=, ExecStartPost=, Environment=).
From those lines, extract bare placeholder NAMES of the form ${MIOS_...}
(with or without a ':-default' tail). Critically we extract only the
PLACEHOLDER inside ${...}; the left-hand `Environment=MIOS_FOO=` literal
(a container-internal env var name being SET) is NOT a placeholder and is
correctly ignored because it is not wrapped in ${...}.

<!-- mios-src:7daebbc3a66d from automation/97-ssot-lint.sh:78-85 -->

### (2) Build the userenv.sh wiring set....

--- (2) Build the userenv.sh wiring set. -------------------------------------
A var is "wired in userenv" if it appears, on a NON-comment line, either as
a typed slot target  ("section.field", "MIOS_X")  -> the quoted token
"MIOS_X"  -- or as an explicit  export MIOS_X=  /  MIOS_X=  assignment, or
named in a legacy for-loop. We strip full-line comments first so a var that
is only *mentioned* in prose (e.g. MIOS_SERVICES_WEBTOOLS_CDP_URL in a doc paragraph)
does NOT count as wired.

<!-- mios-src:bd0d334822c4 from automation/97-ssot-lint.sh:103-109 -->

### (3) Build the render-quadlets.sh allowlist set....

--- (3) Build the render-quadlets.sh allowlist set. --------------------------
A var is "wired in render" if it appears in the envsubst allowlist string
( ${MIOS_X} ) and/or the bash-fallback `for var in ...` list ( MIOS_X ),
on a NON-comment line. Both forms reduce to: the bareword MIOS_X occurs in
render-quadlets.sh code. (render-quadlets.sh also EXPORTS a couple vars
dynamically -- e.g. MIOS_CODE_SERVER_UID via `id -u` -- which the bareword
match likewise accepts.)

<!-- mios-src:8abe5835330f from automation/97-ssot-lint.sh:136-142 -->

### Windows PowerShell 5.1 -- which is what runs the install...

Windows PowerShell 5.1 -- which is what runs the install path on a stock
Windows box -- reads a BOM-less file as ANSI, not UTF-8. Any .ps1 carrying
non-ASCII (the box-drawing run separators, arrows and accented text MiOS
prints) therefore MUST ship a UTF-8 BOM or its output is mojibake. This is the
same convention tools/render-globals.py already writes with (utf-8-sig).
Pure-ASCII scripts need no BOM and must not carry a pointless one.

<!-- mios-src:62f7b82da080 from automation/98-drift-checks.sh:6736-6741 -->

### 'MiOS' overlay -- make BUILDER look/feel like a Live 'MiOS'...

---------------------------------------------------------------------------
'MiOS' overlay -- make BUILDER look/feel like a Live 'MiOS' environment.
Rsyncs the user-facing assets (mios CLI, motd, vendor docs, paths.sh,
profile.d hooks) into the podman-machine without touching its systemd /
sysusers / tmpfiles plumbing (those live only in the bootc image).
---------------------------------------------------------------------------

<!-- mios-src:d0d74784cb5b from automation/mios-build-builder.ps1:219-224 -->

### validate-kargs.py -- 'MiOS' kargs.d schema validator....

validate-kargs.py -- 'MiOS' kargs.d schema validator.

Checks every *.toml in:
  kargs.d/                              (repo root drop-ins)
  usr/lib/bootc/kargs.d/  (image-baked drop-ins)

Schema rules (bootc-dev/bootc authoritative):
  - Top-level key `kargs` (required) must be a list of strings.
  - Top-level key `match-architectures` (optional) must be a list of strings.
  - NO other top-level keys.
  - NO [section] table headers anywhere in the file.
  - Each kargs entry must be a single string (not space-joined multi-arg).
  - Keys with "delete" in their name are invalid parameter -- reject.

Exit codes: 0 = pass, 1 = validation failure(s), 2 = usage error.

<!-- mios-src:ad7c112407e8 from automation/validate-kargs.py:4-20 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Overlay...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Overlay script that maps the /ctx/ source directory onto the rootfs during build, specifically handling the /usr/local to /var/usrlocal symlink logic and syncing the system version file.
AI-related: /usr/share/mios/VERSION, /usr/libexec/mios/motd, /usr/libexec/mios/mios-dashboard.sh, /usr/share/mios/mios.toml, mios-dashboard, mios-infra, mios-bootstrap, wsl-init.service

<!-- mios-src:6ab528c3ee27 from automation/01-system-files-overlay.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Enables external DNF repositories (Terra, Kubernetes, ublue-os COPR) for MiOS by fetching .repo files into /etc/yum.repos.d; use this to ensure availability of non-standard packages like kubectl and uupd.
AI-functions: try_fetch

<!-- mios-src:fd2da0114e6c from automation/06-enable-external-repos.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Configures...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures PAM via authselect, creates the primary system user with fixed UID 1000, and assigns group memberships (wheel, libvirt, docker) to ensure proper session permissions and container access.
AI-related: mios-custom, mios-home, mios-wheel, mios-nfs

<!-- mios-src:249acad8a0b5 from automation/11-user.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Sets the...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Sets the initial hostname template in /usr/lib/hostname.default based on the MIOS_IDENTITY_HOSTNAME build-arg to ensure a unique, stable mios-XXXXX identifier is generated during the first boot.
AI-related: mios-XXXXX, mios-init, mios-a3f9c, mios-ws-83427

<!-- mios-src:8a64ccc13d61 from automation/12-hostname.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures the dynamic PostgreSQL-to-OS user account sync service, enabling live account mappings without the packaging-restricted NSS/PAM pgsql modules.
AI-related: 11-user.sh, schema-init.sql, mios-account-sync.service

<!-- mios-src:1938f88a845f from automation/13-accounts-db.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=dev-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=dev-only
AI-hint: Configures Podman machine backend compatibility by ensuring the 'core' user exists via sysusers and symlinking essential systemd units like podman.socket and qemu-guest-agent.service for container runtime support.
AI-related: podman.socket, qemu-guest-agent.service, sshd.service, cloud-init.service, cloud-final.service, multi-user.target

<!-- mios-src:ccf74554c3ad from automation/14-podman-machine-compat.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs FreeIPA and SSSD packages and enables the mios-freeipa-enroll.service; use this script to provision identity management and verify SSSD file capabilities for zero-touch enrollment.
AI-related: /etc/mios/ipa-enroll.env, mios-freeipa-enroll, mios-freeipa-enroll.service

<!-- mios-src:798634f33f67 from automation/15-freeipa-client.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Configures...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures GPU drivers by installing Mesa, AMD ROCm, and Intel compute runtimes, while performing a multi-stage check and fallback logic for NVIDIA kernel modules based on the current kernel version.
AI-related: mios-kver

<!-- mios-src:34a502301b52 from automation/20-hardware.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs and...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs and configures virtualization (KVM/QEMU/Libvirt), container runtimes (Podman/Buildah), Cockpit management, and CrowdSec security tools to establish the core virtualization and containerization stack.
AI-related: mios-kver, mios-virtio

<!-- mios-src:014912250cf7 from automation/21-virt.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs systemd drop-in files for NVIDIA services to implement ExecCondition guards, ensuring units skip execution if the kernel's nvidia module is not yet registered by akmods/depmod.
AI-related: mios-akmod-guard, systemd.service

<!-- mios-src:47bd81d6c74a from automation/22-akmod-guards.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures GPU passthrough by symlinking systemd unit files for NVIDIA/AMD/Intel drivers into the multi-user.target.wants directory and enabling the container_use_devices SELinux boolean.
AI-related: mios-gpu-status, mios-gpu-nvidia, mios-gpu-amd, mios-gpu-intel, multi-user.target, mios-gpu-status.service, mios-gpu-nvidia.service, mios-gpu-amd.service, mios-gpu-intel.service

<!-- mios-src:44c7907f57aa from automation/23-gpu-passthrough.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=dev-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=dev-only
AI-hint: Configures Hyper-V GPU-PV (dxgkrnl) support by creating mount points, ld.so.conf entries, and a systemd service to detect and bridge host-side GPU drivers for Mesa D3D12 and NVIDIA CUDA.
AI-related: mios-gpu-pv, mios-gpu-pv-detect, mios-gpu-pv-detect.service, display-manager.service, local-fs.target, multi-user.target
AI-functions: log

<!-- mios-src:62c44b589edb from automation/24-gpu-pv-shim.sh:1-5 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs AMD...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs AMD and Intel vendor-specific CDI (Container Device Interface) generator tools (amd-ctk and intel-cdi-specs-generator) to enable multi-vendor GPU passthrough for container runtimes.
AI-related: /usr/libexec/mios/intel-cdi-specs-generator, mios-cdi-detect

<!-- mios-src:61ab07be6bdf from automation/25-gpu-cdi-toolkits.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures and enables systemd units for NVIDIA CDI (Container Device Interface) auto-refresh, removes legacy oci-nvidia-hook.json to prevent conflicts, and ensures the GPU runtime environment is correctly wired for container orchestration.
AI-related: mios-gpu, mios-nvidia-cdi, nvidia-cdi-refresh.service, nvidia-persistenced.service, multi-user.target

<!-- mios-src:7082ca180466 from automation/26-nvidia-cdi-refresh.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Configures...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures Hyper-V Enhanced Session support by enabling hv_sock, configuring gnome-remote-desktop for Wayland-native RDP via vsock, and gating services like nvidia-powerd and waydroid for VM environments.
AI-related: mios-container, mios-hyperv-enhanced, mios-grd-setup, mios-no-audit, polkit.service, cockpit.socket, mios-hyperv-enhanced.service, dbus-broker.service, systemd-machined.service, dev-binderfs.mount

<!-- mios-src:6162b1732f52 from automation/27-vm-gating.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Automatically generates Quadlet configuration files (.pod, .container, .network) from the mios.toml SSOT at image build time.
AI-related: tools/generate-pod-quadlets.py, usr/share/mios/mios.toml, usr/share/containers/systemd/

<!-- mios-src:9dc9177fe79f from automation/33-generate-quadlets.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Processes Quadlet container files by replacing ${MIOS_*} placeholders with values from mios.toml using envsubst, ensuring systemd-compatible container definitions are baked with correct host-specific UIDs, GIDs, and network configs.
AI-related: /usr/share/mios/kb
AI-functions: _render_with_envsubst, _render_with_bash

<!-- mios-src:69517bcb5de2 from automation/34-render-quadlets.sh:1-5 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs Ceph client tools and the K3s Kubernetes orchestrator, handling version resolution and offline vendoring to provision the storage and container orchestration layer of the MiOS cluster.
AI-related: /usr/share/mios/k3s-manifests/, /usr/share/mios/vendored/k3s, /usr/share/mios/vendored/k3s-install.sh, /usr/share/mios/vendored/sha256sum-amd64.txt, /usr/libexec/mios/ceph-bootstrap.sh, mios-ceph-bootstrap, ceph-bootstrap.service, mios-ceph-bootstrap.service, k3s.service, var-home.mount

<!-- mios-src:7087454e58a6 from automation/36-ceph-k3s.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs and enables the moby-engine (Docker) package and its systemd socket to provide container runtime capabilities alongside Podman, resolving package conflicts via the defined moby configuration.
AI-related: docker.socket

<!-- mios-src:73831ce0344b from automation/39-moby-engine.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Configures...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures systemd services, enforces cgroup v2 compliance, fixes unit file permissions, and applies environment-specific gating for bare-metal, VM, and WSL2 deployments.
AI-related: mios-role, bootloader-update.service, podman-auto-update.timer, mios-ceph-bootstrap.service, cockpit.socket, mios-role.service, var-home.mount, var-lib-containers.mount

<!-- mios-src:1886e470b5a4 from automation/41-services.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures firewalld rules via firewall-offline-cmd to open specific TCP ports for MiOS services (Hermes, Open WebUI, Code Server, etc.) based on environment-derived port variables.
AI-related: mios-hermes, mios-open-webui, mios-code-server, mios-guacamole, mios-forge, mios-cockpit-link, mios-adguard, mios-pxe

<!-- mios-src:e22c1962e7c7 from automation/44-firewall-ports.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Configures...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures the system firewall by generating a persistent firewalld init script that maps resolved environment ports (SSH, RDP, K3s, Hermes, Open-WebUI) to the firewall's allowed rules.
AI-related: mios-firewall-init, mios-firewall, mios-hermes, mios-open-webui, mios-code-server, mios-guacamole, mios-forge, mios-cockpit-link

<!-- mios-src:97451191b1b9 from automation/45-firewall.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Configures the host's admin sshd to bind to the SSOT port defined in mios.toml by creating a drop-in config in /etc/ssh/sshd_config.d/ to avoid port conflicts with Forgejo's git-ssh.
AI-related: mios-forge, mios-ssh-port, mios-forge.container

<!-- mios-src:7bd4f590f328 from automation/46-sshd-port.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Enables core MiOS systemd units (mios-role.service and mios-podman-gc.timer) by creating symlinks in multi-user.target.wants to ensure the Unified Role Engine and podman garbage collection are active.
AI-related: /usr/libexec/mios/role-apply, mios-role, mios-podman-gc, mios-role.service, mios-podman-gc.timer, multi-user.target

<!-- mios-src:8b902d31f9af from automation/47-init-service.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs the uupd and greenboot packages, enables the uupd.timer, and disables superseded update timers (bootc-fetch-apply-updates and rpm-ostreed-automatic) to configure the system's update mechanism.
AI-related: uupd.timer, bootc-fetch-apply-updates.timer, rpm-ostreed-automatic.timer, multi-user.target

<!-- mios-src:e9a507001f55 from automation/50-uupd-installer.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Enables and symlinks security services (usbguard, auditd, fapolicyd) into the multi-user.target.wants directory and pre-generates fapolicyd trust databases to harden the system during the build/provisioning phase.
AI-related: mios-hardening, multi-user.target, usbguard.service, auditd.service, fapolicyd.service

<!-- mios-src:153168e72d02 from automation/51-hardening.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Fixes boot-time failures by restoring execution bits on MiOS binaries, correcting USBGuard permissions, resolving systemd-resolved user mappings, and resolving ordering cycles for GPU passthrough.
AI-related: mios-role, mios-cdi-detect, mios-gpu-nvidia, mios-role.service, mios-cdi-detect.service, systemd-resolved.service, docker.socket, mios-gpu-nvidia.service, sockets.target, basic.target

<!-- mios-src:36e65d1db3c4 from automation/52-apply-boot-fixes.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=bake-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Enables the mios-copy-build-log.service systemd unit by creating a symbolic link in multi-user.target.wants to ensure build logs are automatically copied during system startup.
AI-related: mios-copy-build-log, mios-copy-build-log.service, multi-user.target

<!-- mios-src:b44595063196 from automation/53-enable-log-copy-service.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs Geist and Symbols-Only Nerd Fonts to ensure the MiOS dashboard, oh-my-posh prompt, and TTY surfaces render icons and monospace text correctly across both GUI and headless environments.
AI-related: /usr/share/mios/vendored/geist-font.zip, /usr/share/mios/vendored/geist-font, /usr/share/mios/vendored/NerdFontsSymbolsOnly.zip, /usr/share/mios/vendored/nerd-symbols.zip, mios-geist, mios-fontconfig

<!-- mios-src:6e3f7f64c911 from automation/56-fonts.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs the...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs the core GNOME 50 desktop environment, including GDM, Wayland portals, and theme consistency for GTK/Qt, while configuring dconf profiles and disabling tracker indexing.
AI-related: mios-qt-adwaita, mios-cursor-ensure, mios-flatpak-install, mios-flatpak-install.service

<!-- mios-src:7dc75be07690 from automation/57-gnome.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Sets...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Sets executable permissions for the core mios- suite of CLI tools in /usr/bin/ and installs auxiliary scripts like mios-toggle-headless and mios-test to establish the primary MiOS command interface.
AI-related: /usr/libexec/mios/mios-dashboard.sh, /usr/lib/mios/userenv.sh., /usr/lib/mios/userenv.sh, mios-toggle-headless, mios-test, mios-dashboard, mios-dash, mios-env, mios-sync-env, mios-update

<!-- mios-src:b5779d7e1967 from automation/59-tools.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Captures the...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Captures the MIOS_DESKTOP_FLATPAKS build-time variable into a system-level environment file at ${MIOS_USR_DIR}/env.d/flatpaks.env to be consumed by the mios-flatpak-install tool during boot-time setup.
AI-related: /usr/lib/mios/env.d, mios-flatpak-install

<!-- mios-src:c18f7d4f3d5f from automation/60-flatpak-env.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs operator-selected Flatpaks into the system image during the build process to ensure the final deployment (ISO, VHDX, etc.) contains the user's chosen desktop applications without requiring a network connection on first boot.
AI-related: 57-gnome.sh, /usr/share/mios/flatpak-list, /usr/share/mios/vendored/, /usr/lib/mios/state, /usr/lib/mios/state/flatpak-bake.env, mios-flatpak-install

<!-- mios-src:eef939ff9fa7 from automation/61-flatpak-bake.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs the...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs the Oh-My-Posh shell prompt customizer by fetching the latest Go binary from GitHub, placing it in /usr/bin/oh-my-posh for system-wide use by mios-prompt.sh.
AI-related: mios-prompt.sh, /usr/libexec/mios/oh-my-posh/, mios-prompt

<!-- mios-src:04f27106bae5 from automation/62-oh-my-posh.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs Hyprland tiling compositor, XWayland, window routing helpers, and constructs the base layout configuration inside /usr/share/mios/hyprland/hyprland.conf.
AI-related: /usr/share/mios/hyprland/hyprland.conf, /usr/bin/hyprland

<!-- mios-src:e6c9ebae303b from automation/65-bake-hyprland.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=bake-only AI-hint: Installs Qt6...

!/bin/bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Installs Qt6 build-time tools, clones the quickshell repository, compiles it, and deploys the default declarative QML panels in /usr/share/mios/quickshell/.
AI-related: /usr/bin/quickshell, /usr/share/mios/quickshell/Config.qml

<!-- mios-src:912ff3bb34b1 from automation/66-bake-quickshell.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=bake-only AI-hint: Node builder...

!/bin/bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Node builder script to pull the zen-browser surfer repository, download the upstream Firefox codebase, apply structural three-pane browser UI patches, and run native mach compilations.
AI-related: /usr/bin/mios-webshell, /usr/lib/mios/webshell/, usr/share/mios/mios.toml [colors], usr/share/mios/mios.toml [browser_ai], usr/share/mios/mios.toml [ports]

<!-- mios-src:cad73f3e2437 from automation/67-bake-surfer.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=universal AI-hint: Installs the...

!/bin/bash
MIOS_APPLY_CLASS=universal
AI-hint: Installs the unified Hermes-Agent and opencode components into the MiOS agent plane, configuring the shared Python venv, systemd services, and core binaries for direct host-level agent operations.
AI-related: build.sh, /usr/lib/mios/agents/, /usr/lib/mios/agents/.venv, /usr/lib/mios/agents, /usr/share/mios/vendored/hermes-agent, /usr/share/mios/vendored/hermes-agent.zip, /usr/share/mios/vendored/hermes_agent.whl, /usr/share/mios/vendored/, /usr/share/mios/vendored, /usr/share/mios/hermes/plugins/web/miosfetch
AI-functions: _BUILD_SPA

<!-- mios-src:744fd1199c21 from automation/72-hermes-agent.sh:1-5 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint: MiOS...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: MiOS AI model-weight bake for BOTH local /v1 lanes -- llama.cpp GGUFs and the vLLM snapshot. Folded from 38-llamacpp-prep + 38-vllm-prep; each block is independently env-gated (MIOS_LLAMACPP_BAKE_MODELS / MIOS_VLLM_BAKE_MODEL), writes a disjoint SEED_DIR, and only appends to sbom/models.tsv.
AI-functions: (see blocks below)

<!-- mios-src:7fc6bf04d3ec from automation/73-model-prep.sh:1-4 -->

### AI-hint

AI-hint: Bakes GGUF weights into /usr/share/mios/llamacpp/models based on MIOS_LLAMACPP_BAKE_MODELS config to enable the offline mios-llm-light lane; agents use this to ensure local model availability.
AI-related: /usr/share/mios/llamacpp/models, mios-llm-light, mios-llm-light.container

<!-- mios-src:ee4421af5bf0 from automation/73-model-prep.sh:6-7 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=bake-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Configures the `prepare-root.conf` file by reading the `[security].composefs_mode` setting from `mios.toml` to enable/disable fs-verity or standard composefs for the root filesystem.
AI-related: systemd-remount-fs.service
AI-functions: _read_mios_scalar

<!-- mios-src:045a91dfb64b from automation/77-composefs-verity.sh:1-5 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=bake-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Enables and symlinks core greenboot systemd services (health checks, grub2 status, and auto-reboot) and sets execution bits on greenboot check scripts to ensure system health monitoring is active.
AI-related: greenboot-healthcheck.service, greenboot-rpm-ostree-grub2-check-fallback.service, greenboot-grub2-set-counter.service, greenboot-grub2-set-success.service, greenboot-status.service, redboot-auto-reboot.service, multi-user.target

<!-- mios-src:24a3e8afd23f from automation/78-greenboot.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=boot-only AI-hint: Configures...

!/bin/bash
MIOS_APPLY_CLASS=boot-only
AI-hint: Configures boot-time console behavior by enabling getty@tty1, serial-getty@ttyS0, and emergency/rescue shells to ensure accessible text consoles and serial access for remote debugging.
AI-related: mios-console, mios-verbose, tty1.service, emergency.service, rescue.service, ttyS0.service

<!-- mios-src:0a29e4d247a6 from automation/79-boot-config.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=bake-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Projects the sharded bake-plan files (.list) under /usr/lib/mios/bake/plan.d/
AI-related: usr/share/mios/mios.toml, tools/generate-bake-plan.py, usr/libexec/mios/mios-bake-group, automation/98-drift-checks.sh

<!-- mios-src:ce75a92321b3 from automation/85-bake-plan.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=bake-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=bake-only
AI-hint: BOOT-02 OpenSCAP scan-only build gate. Reads [compliance] from mios.toml; when enabled=true it runs `oscap xccdf eval` against an SSG datastream (explicit [compliance].datastream, else ssg-<os-release ID>-ds.xml located from the installed scap-security-guide RPM) under the configured profile, bakes the ARF + HTML reports into [compliance].report_path (in /usr, not /var), then defers the pass/fail verdict to mios-oscap-gate (counts FAILED rules at/above [compliance].severity_gate). DEFAULT OFF + degrade-open: disabled => exits 0 (complete no-op). Scan-only -- openscap-scanner + scap-security-guide are already in [packages.security]; remediation (oscap-im) is intentionally NOT wired. Runs in build.sh numeric order, before the Containerfile's final `bootc container lint`.
AI-related: ../usr/libexec/mios/mios-oscap-gate, lib/packages.sh, lib/common.sh, ../usr/share/mios/mios.toml, build.sh, oscap

<!-- mios-src:bd0fff0b1115 from automation/86-oscap-compliance.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=bake-only AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Finalizes the build by applying systemd presets, setting the default boot target, scrubbing credential leaks, purging DNF caches, and generating the MiOS version metadata files in /usr/lib/mios/.
AI-related: /usr/lib/mios/., /etc/mios/role.conf, /etc/mios/version, mios-version, graphical.target, multi-user.target

<!-- mios-src:df75ba429858 from automation/88-finalize.sh:1-4 -->

### !/bin/bash MIOS_APPLY_CLASS=bake-only AI-hint: Runs Syft to...

!/bin/bash
MIOS_APPLY_CLASS=bake-only
AI-hint: Runs Syft to generate CycloneDX + SPDX SBOM manifests into ${MIOS_USR_DIR}/artifacts/sbom. DEGRADE-OPEN: SBOM is build PROVENANCE, never a build-critical gate -- this script must NEVER fail the image build (always exits 0).
AI-related: mios-sbom, usr/libexec/mios/mios-bake-group (records bound-image digests -> the SBOM provenance), ADR-0003 (SBOM-not-hardcode)

<!-- mios-src:66712f57ae16 from automation/90-generate-sbom.sh:1-4 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: SSOT-render conformance lint -- asserts every ${MIOS_*} placeholder referenced in a Quadlet Exec=/Environment= line has BOTH a typed export/mapping in tools/lib/userenv.sh AND an allowlist entry in automation/34-render-quadlets.sh, so no placeholder silently relies only on its inline shell default (a dead key). Runs standalone or as a build sub-phase; pure bash + grep, no python deps.
AI-related: ./tools/lib/userenv.sh, ./automation/34-render-quadlets.sh, ./usr/share/containers/systemd, /usr/share/mios/mios.toml
AI-functions: _norm_refs, _in_userenv, _in_render, main
automation/97-ssot-lint.sh
----------------------------------------------------------------------------
THE META-FIX (W0-T1). The render pipeline (34-render-quadlets.sh) bakes
${MIOS_*:-default} placeholders in the Quadlet *.container files with the
values resolved from mios.toml by userenv.sh. For that flow to actually
carry an operator's mios.toml value through to a running container, a
placeholder MUST be wired on BOTH ends:

  (a) tools/lib/userenv.sh         -- a typed slot ("section.field","MIOS_X")
                                      (or an explicit export) that EMITS the
                                      MIOS_X env var from mios.toml; AND
  (b) automation/34-render-quadlets.sh -- an allowlist entry (the envsubst
                                      '${MIOS_X}' list AND/OR the bash-
                                      fallback `for var in ...` list) so the
                                      renderer actually substitutes MIOS_X.

A placeholder wired on neither (or only one) end is a DEAD KEY: at render
time it silently collapses to its inline `:-default`, so editing mios.toml
does nothing and the value is un-tunable. This lint walks every Quadlet
Exec=/Environment= line, pulls each referenced ${MIOS_*}, and asserts the
two-sided wiring. It retroactively catches the known dead keys
(MIOS_SGLANG_TOOL_PARSER, MIOS_PORTS_CPU_NODE, MIOS_LLAMACPP_CPU_NODE_THREADS, ...).

Default behaviour: emit a per-key error for every orphan and exit 1 if any
orphan is found (so it can fail a CI/build step). It NEVER mutates anything
-- read-only static analysis. Set MIOS_SSOT_LINT_SOFT=1 to report orphans
but still exit 0 (advisory mode, e.g. while a fix is staged).

Usage:
  automation/97-ssot-lint.sh              # lint, exit 1 on any orphan
  MIOS_SSOT_LINT_SOFT=1 automation/97-ssot-lint.sh   # advisory (exit 0)
  MIOS_SSOT_LINT_ROOT=/path automation/97-ssot-lint.sh  # override repo root

User-agnostic: no User=/uid assumptions, no network, no python.
----------------------------------------------------------------------------

<!-- mios-src:9754d3acf372 from automation/97-ssot-lint.sh:1-40 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Source-tree drift fitness-functions (WS-0A). Read-only static analysis over the repo (== system root) that FAILS on AI-plane SSOT drift no other gate catches: a retired local Ollama lane in active config, a retired model-id (gemma4 / qwen3:1.7b) hardcoded in a
AI-related: 99-postcheck.sh, build.sh, /usr/libexec/mios/mios-ai-hint-coverage, /usr/share/mios/mios.toml, /usr/share/mios/ai/v1, /usr/share/mios/ai, /etc/mios/ai, /usr/lib/mios/agent-pipe, /usr/share/mios/ai/v1/packages, /usr/libexec/mios/mios-registry
AI-functions: python3, _violation, check_dead_lane, check_retired_models, check_structured, check_hint_coverage, check_module_boundary, check_rbac_tiers, check_agent_schema, check_ai_manifest, check_package_registry, check_cli_sql_safety
MIOS_DRIFT_CHECK_SOFT=1 to report but exit 0 (advisory, while a fix is staged).

<!-- mios-src:afe8cb3c5178 from automation/98-drift-checks.sh:1-6 -->

### !/usr/bin/env bash MIOS_APPLY_CLASS=universal AI-hint...

!/usr/bin/env bash
MIOS_APPLY_CLASS=universal
AI-hint: Final build-time validation script that enforces mandatory security invariants, such as OpenSSH version minimums and Cockpit configuration checks, to abort the build if the image is insecure or non-compliant.
AI-related: /usr/share/mios/ai, /etc/mios/ai, mios-ceph, mios-k3s, wsl-init.service
AI-functions: _sysusers_effective, _gid_in_etc_group

<!-- mios-src:845010814b0e from automation/99-postcheck.sh:1-5 -->

### !/bin/bash AI-hint: Initializes the MiOS build environment...

!/bin/bash
AI-hint: Initializes the MiOS build environment by prompting for/loading configuration variables, GitHub PATs, and admin credentials to prepare the local environment for the installation and staging phases.
AI-related: mios-pipeline, mios-build, mios-install-XXXXXX

<!-- mios-src:cb645fc17b62 from automation/bootstrap.sh:1-3 -->

### !/bin/bash AI-hint: This script is the primary installation...

!/bin/bash
AI-hint: This script is the primary installation and ignition tool for MiOS; an agent uses it to clone the MiOS repository and merge its components into the Fedora Server root filesystem.
AI-related: /usr/share/mios/mios.toml.example., /etc/mios/install.env, mios-ignition, localhost:8080
AI-functions: log, log_warn, log_error, log_info, show_banner, collect_user_config, check_prerequisites, install_dependencies, fetch_mios_repo, queue_environment_files, merge_mios_structure, create_user_account

<!-- mios-src:04791f399af5 from automation/build-mios.sh:1-4 -->

### !/bin/bash AI-hint: This script is the primary build runner...

!/bin/bash
AI-hint: This script is the primary build runner for MiOS, managing the build lifecycle by parsing `mios.toml` configurations, enforcing environment constraints, and rendering a TTY-safe ASCII progress UI for the build process.
AI-related: /etc/mios/mios.toml, /usr/share/mios/mios.toml, mios-bootstrap, mios-build, mios-step, packagekit.service
AI-functions: _pad, _hline, _row, _progress_bar, _step_header, _step_result, _section_header, _progress_frame, _fail_report, _warn_report, _final_summary

<!-- mios-src:3a7c439788e2 from automation/build.sh:1-4 -->

### !/usr/bin/env bash AI-hint: Executes the full MiOS system...

!/usr/bin/env bash
AI-hint: Executes the full MiOS system bootstrap to transform a bare Fedora host into a complete MiOS workstation by installing all core components, configuring FHS paths, and setting up the environment.
AI-related: packages.sh, /usr/share/mios/mios.toml, mios-dev, mios-stage-XXXXXX
AI-functions: log_info, log_ok, log_warn, log_err, log_phase, require_root, detect_host_kind, check_network, prompt_default, prompt_password, prompt_yesno, main

<!-- mios-src:4ebf3210e5a8 from automation/install-bootstrap.sh:1-4 -->

### !/usr/bin/env bash...

!/usr/bin/env bash
MIOS_INSTALLER_ROLE=fhs-overlay-installer
AI-hint: Installs the MiOS FHS overlay onto non-bootc Fedora hosts by syncing usr/etc/var/srv directories, materializing /v1 symlinks, and initializing systemd users, tmpfiles, and services.
AI-related: install.sh

<!-- mios-src:9423ab4cd7be from automation/install-fhs.sh:1-4 -->

### !/usr/bin/env bash...

!/usr/bin/env bash
MIOS_INSTALLER_ROLE=container-build-installer
AI-hint: Thin redirector to install-fhs.sh -- the single FHS-overlay installer for non-bootc Fedora hosts (rsyncs usr/etc/var/srv onto /, materializes /v1, runs sysusers/tmpfiles, reloads systemd). install.sh and install-fhs.sh were byte-identical; deduped to ONE implementation. Superseded by `mios-apply fhs-host` once the unified git=$ROOT engine lands.

<!-- mios-src:3b89614343c1 from automation/install.sh:1-3 -->

### !/usr/bin/env bash AI-hint: Python py_compile +...

!/usr/bin/env bash
AI-hint: Python py_compile + undefined-name gate over EVERY tracked Python file in the repo (git ls-files, plus extensionless python-shebang entry points; rendered templates excluded). Directory-by-directory enumeration is what let the canonical OWUI pipe sit outside the gate while it did not import at all.

<!-- mios-src:e1f1e6bbbcc4 from automation/lint-python.sh:1-2 -->

### AI-hint

AI-hint: Idempotent PowerShell script to provision a rootful Podman machine named 'mios-builder' with full host CPU/RAM/GPU passthrough and nvidia-container-toolkit setup for Windows-based MiOS builds.
AI-related: mios-builder, mios-bootstrap, mios-dev, mios-build-builder
AI-functions: Log, Warn, Die, Invoke-MachineSSH
Requires -Version 7.1

<!-- mios-src:1c20ee661e49 from automation/mios-build-builder.ps1:1-4 -->

### !/usr/bin/env bash AI-hint: Configures the MiOS-DEV podman...

!/usr/bin/env bash
AI-hint: Configures the MiOS-DEV podman machine by syncing system files, creating service users, setting up tmpfiles, and configuring subuid/subgid to mirror a production MiOS environment for hosting Quadlets.
AI-related: /usr/lib/mios/logs, mios-forge, mios-guacamole, mios-pxe-hub, mios-crowdsec, mios-guacd, mios-postgres, mios-virt, mios-tmpfiles-prereq
AI-functions: _rsync_in

<!-- mios-src:55c281841172 from automation/overlay-builder.sh:1-4 -->

### !/usr/bin/env python3 AI-hint: Validates .toml files in...

!/usr/bin/env python3
AI-hint: Validates .toml files in kargs.d/ and usr/lib/bootc/kargs.d/ against bootc schema rules, ensuring correct key structures, architecture matching, and no forbidden table headers.
AI-functions: _github_error, _github_warning, validate_file, _emit, collect_files, main

<!-- mios-src:eb7085e80097 from automation/validate-kargs.py:1-3 -->

### `miosd harden` is one function serving BOTH this stage and...

`miosd harden` is one function serving BOTH this stage and 51: it rewrites
trust= (this stage's job) and enables usbguard/auditd/fapolicyd (51's). The
old leg ran it and then `exit 0`, which skipped the `systemctl enable` below.
That is not the same thing: miosd writes the multi-user.target.wants symlink
directly, while `systemctl enable` reads [Install] and honours whatever else
it declares. fapolicyd is not installed on the machine this was converted on,
so that equivalence could not be measured -- and an unmeasured equivalence is
not one. The enable stays exactly where it was, after either leg.

<!-- mios-src:36d96bd5efd8 from automation/40-fapolicyd-trust.sh:26-33 -->

### Absolute path, never `command -v`: miosd installs to...

Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
nothing puts on PATH at bake time, so the lookup this replaced could never
succeed (task_962). Note the elif below is dead too: SYSFILES is
/ctx/system_files, and the Containerfile builds /ctx from automation/, usr/,
etc/, tools/ and VERSION -- it never creates a system_files/ directory, and
the repo has none. Both non-default branches were unreachable, so policy.json
arrived purely as an overlay copy and this stage generated nothing.

<!-- mios-src:f4717cb51488 from automation/49-cosign-policy.sh:50-56 -->

### Both paths run the same generator

Both paths run the same generator: miosd render-uki-cmdline execs
tools/generate-uki-cmdline.py, which is where the kargs.d parse actually
lives. The dispatch is about which side owns the invocation, not about two
implementations -- so there is no output to diff, only a root to get right.
miosd resolves the script against MIOS_ROOT, so pass it explicitly rather
than relying on the caller's cwd.

<!-- mios-src:cb2dc2e83237 from automation/76-uki-render.sh:41-46 -->

### task_969. Windows-only shim

task_969. Windows-only shim: where a host ships `python` but no `python3`,
materialise one under that name so the checks below can call it. Two rules
make it safe, and BOTH were missing:
  1. Never when a real python3 already resolves. On Linux it always does, so
     this whole block must no-op there.
  2. Never reuse a cached copy. The copy lives in TEMP and outlives
     interpreter upgrades; a stale one puts every check on a DIFFERENT
     interpreter than sync-generated.sh, just, CI and the operator's own
     `python3 tools/...`, and silently absorbs version-dependent failures.

<!-- mios-src:18bb33a2f550 from automation/98-drift-checks.sh:8-16 -->

### Every value here used to come from ${MIOS_CONV_*:-literal}....

Every value here used to come from ${MIOS_CONV_*:-literal}. Nothing exports
MIOS_CONV_* -- not globals.sh, not run-suites.sh, not this script -- so the
check validated its own hardcoded defaults on every run and never opened
mios.toml at all. The defaults had already drifted from the SSOT:
retire_heavy_alt is true in [converge.inference] but defaulted to false
here, which permanently skipped the one assertion that inspects a real
systemd unit; cold_retention_days is 90 against an asserted 30; and
cold_zstd_level is 10 against an asserted 3.

Read the SSOT. An env var may still override for testing, but the FALLBACK
is now the SSOT value rather than a literal, so the check cannot silently
grade a file it never read.

<!-- mios-src:03520b292756 from automation/98-drift-checks.sh:653-664 -->

### The lint prints its own tally -- N of M consumed, K...

The lint prints its own tally -- N of M consumed, K registered unconsumed.
This wrapper used to answer it with "all ... have code consumers", which
was the overclaim the lint itself was making when it walked a hardcoded
nine of 128 keys (task_984). Do not reintroduce a summary here that asserts
more than the tool it wraps just measured.

<!-- mios-src:7ea252901d10 from automation/98-drift-checks.sh:836-840 -->

### Was

Was: grep the whole FILE for "|| true" (or set +e / trap / exit 0) and
call that degrade-open. File-global, so one unrelated cleanup guard
certified the script; all thirteen passed and the gate could not fail,
while forge-firstboot.sh really did abort firstboot on an unreachable
Forgejo API. The tool scopes the question to the egress calls themselves.

<!-- mios-src:63cfee7b6954 from automation/98-drift-checks.sh:1196-1200 -->

### The bash leg must run a DIFFERENT implementation, or this...

The bash leg must run a DIFFERENT implementation, or this check compares
mios_toml.py against itself. userenv.sh resolves in three tiers -- native
mios-resolver, miosd, then the Python fallback -- and under `env -i` with
no binary on PATH it reached tier 3, so mutating mios_toml.py changed BOTH
legs and they went on agreeing. Proven by mutation: disabling
resolve_cross_references in mios_toml.py left the bash leg emitting
${MIOS_PORTS_AGENT_PIPE} verbatim, and the check still passed (task_3360).

Locate the native resolver and put it on the fixture's PATH so tier 1
fires. Absent, the comparison is vacuous: fail where the environment
declares tools mandatory, and say plainly what went unverified otherwise.

<!-- mios-src:83d75fddcaa5 from automation/98-drift-checks.sh:1244-1254 -->

### emit_exports() is the resolver Law 13 names. Reading...

emit_exports() is the resolver Law 13 names. Reading section(load_merged())
instead compared the bash RESOLVER against a raw table read -- not twin
against twin, which is why no cross-reference could ever disagree here: this
leg never ran the code that resolves one.

<!-- mios-src:6aefbbd7b328 from automation/98-drift-checks.sh:1308-1311 -->

### Two empty sets compare equal, and the fixture above sets...

Two empty sets compare equal, and the fixture above sets endpoint,
model and embed_model, so emitting nothing means BOTH resolvers are
broken -- previously reported as a pass.

<!-- mios-src:2050f85cc226 from automation/98-drift-checks.sh:1316-1318 -->

### This check used to `cp -r` the committed kargs.d into the...

This check used to `cp -r` the committed kargs.d into the "expected"
directory and then render into that same copy, so 15 of the 17 files were
diffed against copies of themselves and could only ever match. Its
Extra/Missing branches were unreachable for the same reason, and the
renderer's exit status was discarded, so a completely broken renderer
still printed the PASS line.

75-kargs-render.sh is an in-place mutator, not a whole-directory
generator: it manages exactly two files -- it rewrites 01-mios-vfio.toml
when present, and writes or REMOVES 99-mios-kargs.toml depending on
whether [kargs] declares custom arguments. The other 15 files are
hand-maintained and are not projections of anything, so this check does
not claim to verify them.

<!-- mios-src:503d1db46fed from automation/98-drift-checks.sh:1369-1381 -->

### [legibility].max_tracked_mb is generated, so the gate that...

[legibility].max_tracked_mb is generated, so the gate that matters is not
"is it big enough" -- check_legibility_ratchet asks that -- but "is the
committed number still what the tree implies". Outside the band, the value
is either already breached or carrying slack nobody declared.
No env override here on purpose. MIOS_GATE_BIN exists and is grandfathered
on the var-closure ledger; adding a second name nothing emits is exactly
what that ledger's header forbids, and check_var_closure caught this one
the moment it was written.

<!-- mios-src:8e8c39e21261 from automation/98-drift-checks.sh:1898-1905 -->

### Ported to mios-gate per ADR-0021; the python twin is...

Ported to mios-gate per ADR-0021; the python twin is deleted in the same
commit, with both paths proved equal first: 78 ceilings on each side, and
the same key named with the same exit code on a planted raise. The port
also carries the [drift.generated_ceilings] exemption, which a ceiling
that is GENERATED rather than hand-maintained needs -- and which must be
itemised with a reason, never a bare name.

<!-- mios-src:88c6612e99d2 from automation/98-drift-checks.sh:1968-1973 -->

### Ported to mios-gate per ADR-0021; the python twin is...

Ported to mios-gate per ADR-0021; the python twin is deleted in the same
commit. The successor is strictly stronger: the old reader matched a
99-postcheck.sh target as a bare SUBSTRING, which a comment after `exit 0`
satisfied for four laws, and it silently dropped both a bare second
enforcer in a comma list and any unrecognised enforcer kind.

<!-- mios-src:29615be54a23 from automation/98-drift-checks.sh:3185-3189 -->

### Law 8 for the container signature policy....

Law 8 for the container signature policy. usr/lib/containers/policy.json
is projected from [security.sigstore] and, until task_984's sweep, was the
ONE generator in the tree with neither half of the law: no regenerate step
and no drift check. Its own generator's --check compared parsed JSON, so
it could not see the tracked file drifting in bytes from what the writer
emits -- which it had.

<!-- mios-src:31dacd9e02b5 from automation/98-drift-checks.sh:3451-3456 -->

### main() dispatches each check as a bare statement under the...

main() dispatches each check as a bare statement under the file's
`set -euo pipefail`, and 189 checks END with _violation, which returns 1.
So the first failing check aborted the whole run: of 207 dispatched
checks only the first 12 ever executed, and the aggregate summary below
was unreachable whenever it had anything to report. Accumulate instead;
VIOLATIONS is the signal, not the exit status of the last check.

<!-- mios-src:56f047239147 from automation/98-drift-checks.sh:3667-3672 -->

### This tests EXISTENCE and nothing else. It used to say...

This tests EXISTENCE and nothing else. It used to say "present and
verified" while claiming to compare resolver logic: a globals.ps1 with a
port changed to 9999 passed it rc=0, and check_globals_generated caught
that same plant rc=1.

<!-- mios-src:4ac4f61abe48 from automation/98-drift-checks.sh:3992-3995 -->

### Ported to mios-gate per ADR-0021; the python twin is...

Ported to mios-gate per ADR-0021; the python twin is deleted in the same
commit. The register now pins path:KEY=VALUE, so a grandfathered KEY whose
VALUE becomes an operator's real password is a NEW finding (task_972).

<!-- mios-src:f78fa7ea982c from automation/98-drift-checks.sh:4502-4504 -->

### Header integrity

Header integrity: a tagger must never absorb line 1. See task_2248.
--- no AI-hint tagger has absorbed a shebang or a MIOS_* build directive from line 1 ---

<!-- mios-src:b6768304192e from automation/98-drift-checks.sh:4836-4837 -->

### stderr carried the only record of a dropped variable and...

stderr carried the only record of a dropped variable and was discarded
here, so MIOS_AI_ENDPOINT going missing from install.env was invisible at
bake while Law 5 routed every agent through it (task_997). It is captured
and echoed now -- on failure AND on success, because a declared
[security.non_bare_env] skip is a thing a reader should still see.

<!-- mios-src:8bbbe72c2e67 from automation/99-postcheck.sh:519-523 -->
### toolchain

--toolchain: provision the SSOT Rust toolchain system-wide and stop, so every
MiOS image can rebuild its native catalog. The Containerfile runs it after
[packages.self-build] (Fedora's rustup RPM ships rustup-init only). Installs the
[build.toolchain] channel and components with this machine's
[build.native.linux] target into [build.toolchain].rustup_home/cargo_home, then
proves the target's std and the linker are there; any gap fails the build.

<!-- mios-src:b09c7374cc50 from automation/55-native-build.sh:7-12 -->

### Every value here used to come from ${MIOS_CONV_*:-literal}....

Every value here used to come from ${MIOS_CONV_*:-literal}. Nothing exports
MIOS_CONV_* -- not globals.sh, not run-suites.sh, not this script -- so the
check validated its own hardcoded defaults on every run and never opened
mios.toml at all. The defaults had already drifted from the SSOT:
cold_retention_days is 90 against an asserted 30, and cold_zstd_level is
10 against an asserted 3. (The heavy-alt retirement branch went with the
lane itself: the one heavy lane carries its engine as [ai].heavy_engine.)

Read the SSOT. An env var may still override for testing, but the FALLBACK
is now the SSOT value rather than a literal, so the check cannot silently
grade a file it never read.

<!-- mios-src:73cbc909c116 from automation/98-drift-checks.sh:634-644 -->

### The findings go out BEFORE _violation

The findings go out BEFORE _violation: it returns 1, and in
single-check mode errexit ends the script there, so anything printed
after it never reached the operator (or a negative test reading why).

<!-- mios-src:bd8a7c2a418f from automation/98-drift-checks.sh:784-786 -->

### Ported to mios-gate per ADR-0021 / task_953 unit 2; the...

Ported to mios-gate per ADR-0021 / task_953 unit 2; the Python twin is
deleted in the same commit. Parity proved on the real tree: both sides
verify 131 verbs clean.

<!-- mios-src:94b1e0f1d814 from automation/98-drift-checks.sh:1023-1025 -->

### The bash leg must run a DIFFERENT implementation, or this...

The bash leg must run a DIFFERENT implementation, or this check compares
mios_toml.py against itself. userenv.sh resolves in three tiers -- native
mios-resolver, miosd, then the Python fallback -- and under `env -i` with
no binary on PATH it reached tier 3, so mutating mios_toml.py changed BOTH
legs and they went on agreeing. Proven by mutation: disabling
resolve_cross_references in mios_toml.py left the bash leg emitting
${MIOS_PORTS_AGENT_PIPE} verbatim, and the check still passed (task_3360).

Locate the native resolver and put it on the fixture's PATH so tier 1
fires. Absent, the comparison is vacuous: fail where the environment
declares tools mandatory, and say plainly what went unverified otherwise.

<!-- mios-src:d4ade8ef15b7 from automation/98-drift-checks.sh:1300-1310 -->

### task_3488 / ADR-0021

task_3488 / ADR-0021: the [rust.categories] registry is the port plan -- this
gate fails it the moment a category loses its owner, a crate vanishes from
disk while still cataloged, a replaces= claim outlives its script, or a
universe script stops being owned by exactly one porting category.

<!-- mios-src:a119d7969c11 from automation/98-drift-checks.sh:2097-2100 -->

### Ported to mios-gate per ADR-0021 / task_953 unit 1; the...

Ported to mios-gate per ADR-0021 / task_953 unit 1; the Python twin is
deleted in the same commit. Parity proved on the real tree: both sides
flagged check_static_linkage identically before the exemption landed,
and both pass after it.

<!-- mios-src:1b2def083b62 from automation/98-drift-checks.sh:3252-3255 -->

### Law 8 for usr/share/mios/ai/v1/metadata.json. It is...

Law 8 for usr/share/mios/ai/v1/metadata.json. It is exported from every
tracked file's AI-* header, but nothing regenerated it and nothing
compared it, so it fell ~2,400 lines behind main and the first unrelated
re-export dragged that whole backlog into a small PR. The exporter's own
--check validates schema only; --check-fresh regenerates in memory and
compares bytes, naming each entry that moved.

<!-- mios-src:e4da726b6ea7 from automation/98-drift-checks.sh:3870-3875 -->

### The warning-level pass is a ratchet

The warning-level pass is a ratchet: it lints what changed since a base.
CI exports MIOS_RATCHET_BASE (the PR base, fetched beside a depth-1 checkout
that has neither origin/main nor HEAD~1), so it wins; origin/main, then
HEAD~1, serve a full clone. An explicit base that does not resolve is a
misconfiguration, not a reason to lint nothing. Paths are repo-relative, so
the pass runs from ROOT; a worktree's .git is a file, hence -e.

<!-- mios-src:52cdadb2df41 from automation/lint-shell.sh:72-77 -->
### These generators' Python twins were ported and deleted...

These generators' Python twins were ported and deleted ([rust.categories.gen].replaces);
a missing native binary is a violation, never a fallback to a script that no longer exists.

<!-- mios-src:4836d6daebbb from automation/98-drift-checks.sh:128-129 -->
