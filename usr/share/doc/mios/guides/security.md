<!-- AI-hint: Documentation of MiOS security hardening posture, mapping kernel boot parameters, sysctl values, SELinux modules/booleans, firewalld ports, and supply-chain controls to the exact files that enforce them; frames hardening as the trust layer beneath the immutable bootc image and the local agentic AI stack.
     AI-related: mios-hardening, mios-firewall-init, mios-ci, mios-dev, 33-firewall, 37-selinux, 12-virt, prepare-root.conf -->
# 'MiOS' Security Hardening

## Purpose

MiOS is a single thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is one container image you `bootc upgrade`
like a `git pull` and `bootc rollback` like a Ctrl-Z) that is *also* a **local,
self-hosted agentic AI operating system** — a full inference + agent stack
(`mios-llm-light` and gated heavy GPU lanes, the agent-pipe/MiOS-Hermes
orchestrator, PostgreSQL+pgvector memory, MCP/A2A) running on the operator's own
hardware behind one OpenAI-compatible endpoint.

Both halves raise the security stakes. The image-mode half means the root
filesystem is content-addressed and tamper-evident, so verification has to extend
from the OCI layer all the way down to per-file integrity. The agentic half means
the machine runs code on the operator's behalf and reasons about itself, so the
agent plane must be unprivileged, network-fenced, and sandboxed by default. This
document is the defense-in-depth map for that whole: every control below cites
the exact file that enforces it, and the rightmost column gives the admin
override.

The posture is sourced primarily from SecureBlue's audit framework
(<https://github.com/secureblue/secureblue>) and the Fedora hardening guidelines
(<https://docs.fedoraproject.org/en-US/quick-docs/securing-fedora/>), filtered so
it does **not** break the NVIDIA/CUDA, ROCm, Steam, libvirt/VFIO, and rootless
Podman workloads MiOS is built to run.

## Kernel boot parameters

Shipped as additive `kargs.d/*.toml` files under `usr/lib/bootc/kargs.d/` —
notably `00-mios.toml` (core/IOMMU), `01-mios-hardening.toml`
(SecureBlue-adapted mitigations), `30-security.toml` (NVIDIA-safe lockdown), and
`31-secureblue-extended.toml` (extended hardening). bootc renders all
`kargs.d/*.toml` into the active kernel cmdline at upgrade time, processed in
lexicographic order; later files are additive (never set the same key to
conflicting values across files — see the lockdown note below).

| Parameter | File | Purpose | Override |
|---|---|---|---|
| `slab_nomerge` | 01/30 | Prevent slab cache merging (heap isolation) | Remove from kargs.d TOML |
| ~~`init_on_alloc=1`~~ | 01 (commented) | Disabled -- interferes with large CUDA allocations; uncomment for CPU-only builds | Higher-priority kargs.d file |
| ~~`init_on_free=1`~~ | 01 (commented) | Disabled -- same CUDA incompatibility | Higher-priority kargs.d file |
| ~~`page_alloc.shuffle=1`~~ | 01 (commented) | Disabled -- NVIDIA driver instability | Higher-priority kargs.d file |
| `randomize_kstack_offset=on` | 01 | Per-syscall kernel stack randomization | `=off` |
| `pti=on` | 01 | Page Table Isolation (Meltdown) | `=off` (not recommended) |
| `vsyscall=none` | 01 | Disable legacy vsyscall table | `=emulate` |
| `spectre_v2=on` / `spectre_bhi=on` | 01 | Spectre v2 / branch-history-injection mitigation | Performance cost ~2-5% |
| `spec_store_bypass_disable=on` | 01 | Spectre v4 SSB mitigation | Performance cost ~1-2% |
| `l1tf=full,force` | 01 | L1TF mitigation | Affects HyperThreading |
| `gather_data_sampling=force` | 01 | GDS/Downfall mitigation | Intel-specific |
| `tsx=off` | 01 | Disable TSX (TAA attack surface) | Remove |
| `kvm.nx_huge_pages=force` | 01 | iTLB-multihit guest mitigation | Remove |
| `lockdown=integrity` | 30 | Kernel lockdown (NVIDIA-safe; allows MOK-enrolled signed modules) | Remove to allow unsigned modules |
| `page_poison=1`, `slub_debug=FZ` | 31 | Poison freed pages / SLUB red-zoning | Remove |
| `debugfs=off`, `oops=panic` | 31 | Disable debugfs; panic on oops | Remove |
| `itlb_multihit=flush,force`, `tsx_async_abort=full,force`, `mds=full,force` | 31 | iTLB-multihit / TAA / MDS mitigations | Remove |
| `iommu=force`, `iommu.strict=1`, `iommu.passthrough=0` | 31 | Strict IOMMU enforcement | Relax for perf |
| `random.trust_bootloader=off`, `random.trust_cpu=off` | 31 | Don't trust bootloader/CPU RNG seed | Remove |
| `efi=disable_early_pci_dma` | 31 | Block early-boot PCI DMA | Remove |
| `iommu=pt` / `amd_iommu=on` | 00 | IOMMU passthrough for VFIO | Required for GPU passthrough |
| `intel_iommu=on` | 01-mios-vfio / 20-vfio | Enable Intel IOMMU | Required for VFIO |
| `nvidia-drm.modeset=1` | 10-mios / 10-nvidia | NVIDIA DRM modesetting (Wayland) | Required for GNOME Wayland |

The `lockdown=integrity` in `30-security.toml` deliberately overrides
`01-mios-hardening`'s stricter `confidentiality` so that the ucore-hci signed
NVIDIA modules (enrolled via Universal Blue MOK) can load; `module.sig_enforce`
stays on, because MOK-enrolled keys are sufficient. This is the concrete
expression of the dual-nature trade-off: hard kernel lockdown that still lets the
GPU lanes and passthrough VMs claim their hardware.

Source: kernel admin-guide,
<https://www.kernel.org/doc/html/latest/admin-guide/kernel-parameters.html>.

## Sysctl hardening

Shipped via `usr/lib/sysctl.d/99-mios-hardening.conf` (selective SecureBlue
subset, filtered to preserve NVIDIA/CUDA/Steam/libvirt). Admin overrides go in
`/etc/sysctl.d/` (Architectural Law 1 — `/etc/` is admin-override only).

### Kernel pointer, debug, and BPF restrictions

| Sysctl | Value | Purpose |
|---|---|---|
| `kernel.kptr_restrict` | 2 | Hide kernel pointers from all users |
| `kernel.dmesg_restrict` | 1 | Restrict dmesg to root |
| `kernel.yama.ptrace_scope` | 2 | Allow parent-child ptrace only |
| `kernel.sysrq` | 0 | Disable Magic SysRq (physical-console attack vector) |
| `kernel.printk` | 3 3 3 3 | Mute kernel messages to `/dev/console` (audit/journal still receive everything) |
| `kernel.unprivileged_bpf_disabled` | 1 | Block *unprivileged* eBPF load |
| `net.core.bpf_jit_harden` | 2 | Harden JIT-compiled BPF against spray attacks |
| `-kernel.unprivileged_userns_clone` | 1 | Keep unprivileged userns **on** — required by rootless Podman, Waydroid, Steam sandboxes (the `-` prefix suppresses the error on kernels such as WSL2 that lack the key) |

`unprivileged_bpf_disabled` and the userns line illustrate the filtering
principle: rootless Podman, K3s, and CrowdSec all use BPF/userns as root, so MiOS
hardens the *unprivileged* path while leaving its own workloads working.

### Network hardening

| Sysctl | Value | Purpose |
|---|---|---|
| `net.ipv4.tcp_syncookies` | 1 | SYN flood protection |
| `net.ipv4.conf.all.accept_redirects` | 0 | Block ICMP redirects |
| `net.ipv4.conf.all.send_redirects` | 0 | Don't send ICMP redirects |
| `net.ipv4.conf.all.rp_filter` / `net.ipv4.conf.default.rp_filter` | 1 | Reverse-path filtering |
| `net.ipv4.conf.all.accept_source_route` | 0 | Block source-routed packets |
| `net.ipv4.icmp_echo_ignore_broadcasts` | 1 | Ignore broadcast pings |
| `net.ipv4.icmp_ignore_bogus_error_responses` | 1 | Ignore bogus ICMP error replies |

IPv6 equivalents are set for `accept_redirects` and `accept_source_route`.

### Filesystem protection

| Sysctl | Value | Purpose |
|---|---|---|
| `fs.suid_dumpable` | 0 | No core dumps for SUID binaries |
| `fs.protected_hardlinks` | 1 | Restrict hardlink creation |
| `fs.protected_symlinks` | 1 | Restrict symlink following |
| `fs.protected_fifos` | 2 | Restrict FIFOs in sticky dirs |
| `fs.protected_regular` | 2 | Restrict regular files in sticky dirs |

`kernel.core_pattern` is left untouched to preserve systemd-coredump
integration, and core dumps are kept on for normal processes (CUDA crashes need
cores) while suppressed for set-uid binaries.

Source: kernel admin-guide / sysctl,
<https://www.kernel.org/doc/Documentation/sysctl/>.

## SELinux

Mode: enforcing. MiOS does not ship one monolithic policy; instead
`automation/38-selinux.sh` runs `restorecon` over `/boot /etc /usr /var`, imports
booleans + fcontexts via `semanage`, and compiles a set of **per-rule** custom
modules (one `.te` per known denial) that are staged as `.pp` packages into
`usr/share/selinux/packages/mios/`. Each module targets a specific Fedora
Rawhide / systemd denial and is skipped gracefully if its type is absent from the
running policy. New rules are added in `automation/38-selinux.sh` (Architectural
Law 1 — per-rule, not monolithic; see also the engineering guide).

Representative compiled modules (staged as `mios_<name>.pp`):

| Module | Purpose |
|---|---|
| `mios_bootupd` / `mios_bootupd_state` | bootupd read/state access to `/boot` |
| `mios_accountsd` / `mios_accountsd_homed` / `mios_accountsd_malcontent` / `mios_accountsd_watch` | accounts-service over systemd-homed / malcontent / `/usr` |
| `mios_resolved` / `mios_resolved_hook` | systemd-resolved socket access |
| `mios_fapolicyd` / `mios_fapolicyd_gdm` / `mios_fapolicyd_grd` | fapolicyd under GDM / GNOME Remote Desktop |
| `mios_portabled` | systemd-portabled D-Bus for sysext/confext |
| `mios_kvmfr` | Looking Glass shared-memory device access |
| `mios_chcon` / `mios_chcon_macadmin` | chcon `mac_admin` capability |
| `mios_coreos_bootmount` | CoreOS boot-mount labeling |
| `mios_gdm_cache` / `mios_gdm_session_cache` | GDM cache-home access |
| `mios_homed_varhome` | systemd-homed `/var/home` traversal |

Booleans imported at build time (via `semanage import`):
`container_manage_cgroup`, `container_use_cephfs`, `daemons_dump_core`,
`domain_can_mmap_files`, `virt_sandbox_use_all_caps`, `virt_use_nfs`,
`virt_use_samba`, `nis_enabled`. A runtime-applied boolean
(`container_use_devices=on`) is staged in
`usr/share/selinux/packages/mios/booleans.conf` and set on first boot by
`usr/libexec/mios/selinux-init` — required for the GPU-container CDI flow (NVIDIA,
ROCm, Intel xe), since `semanage` is typically inoperative inside an OCI build.

Fcontexts added: `boot_t` on `/boot/bootupd-state.json`, `accountsd_var_lib_t`
on the accountsservice interfaces tree, `ceph_var_lib_t` / `ceph_log_t` on the
Ceph data/log trees, and `xdm_var_lib_t` on `/var/lib/gnome-remote-desktop`.

Status:

```bash
getenforce
ausearch -m AVC -ts recent
semodule -l | grep mios
```

## Firewall

`firewalld` default-deny: default zone `drop` (all inbound denied), with an
explicit allow-list rendered into `/usr/libexec/mios-firewall-init` by
`automation/33-firewall.sh`. All port values resolve through the layered SSOT
(`mios.toml [ports]` → `tools/lib/userenv.sh` → `MIOS_PORT_*` env vars) and are
baked into the runtime script at build time — hardcoded port literals are bugs.

Allowed services: `cockpit`, `ssh`, `mdns`, `samba`, `nfs`, `rpc-bind`,
`mountd`. Allowed ports include the host admin sshd (`MIOS_PORTS_SSH`, hardened
off `:22` to `2222`), RDP (`MIOS_PORTS_RDP` + `3390` for the Hyper-V vsock),
libvirt (`16509`), VNC (`5900-5999`), K3s API + kubelet
(`MIOS_PORTS_K3S_API` + `10250`), Pacemaker/Corosync (`2224`, `5403-5405/udp`),
and the AI/web plane: MiOS-Hermes (`MIOS_PORTS_HERMES`, the canonical
OpenAI-API endpoint — Architectural Law 5), Open WebUI
(`MIOS_PORTS_OPEN_WEBUI`), code-server, Guacamole, Forge HTTP + git-ssh, and the
Cockpit link shim. Internal interfaces (`lo`, `podman+`, `br-+`, `veth+`,
`virbr0`, `cni0`, `flannel.1`, `waydroid0`) are placed in the `trusted` zone via
wildcards because the nftables backend strictly drops unassigned interfaces.
Cockpit is reachable from the `public`, `libvirt`, and `trusted` zones. CrowdSec's
nftables bouncer integrates on top of this default-deny base.

```bash
firewall-cmd --list-all
firewall-cmd --list-all-zones
```

## CrowdSec

Sovereign/offline IPS. `automation/12-virt.sh` (lines ~51-58) comments out
`online_client` in `/etc/crowdsec/config.yaml` so no Central API is contacted —
the upstream contract puts the file under `/etc/`, with no `/usr/lib` drop-in
mechanism, so this edit is a deliberate exception to USR-OVER-ETC. CrowdSec
monitors the journal and applies local-only nftables bans through the bouncer.

```bash
sudo cscli metrics
sudo cscli decisions list
sudo cscli alerts list
```

## fapolicyd

Trust rules in `etc/fapolicyd/fapolicyd.rules`. Deny-by-default execution
control: blocks any binary not in the trust database, which complements the
immutable image (the trusted set is essentially what shipped in `/usr`).

```bash
systemctl status fapolicyd
fapolicyd-cli --dump-db | head -20
```

## USBGuard

Off by default. To enable, generate a policy from currently-connected devices:

```bash
sudo usbguard generate-policy > /etc/usbguard/rules.conf
sudo systemctl restart usbguard
sudo usbguard list-devices
sudo usbguard allow-device <id>
```

## composefs / image integrity

This is the foundation under the immutable half of MiOS — the control that makes
"the repo root IS the deployed system root" verifiable at boot. Configured in
`usr/lib/ostree/prepare-root.conf`:

```toml
[composefs]
enabled = verity

[sysroot]
readonly = true

[etc]
# left persistent (NOT transient) — workstation needs persistent SSH/NM/user state
```

`enabled = verity` requires fsverity signatures on every file in `/usr`, making
the root tamper-evident: the OS refuses to boot a composefs image whose digests
don't match the expected manifest, which is the core tenet of image-mode
integrity. The ucore-hci base is built with fsverity digests to support this. It
requires ext4 or btrfs at install time (xfs does not support fsverity); if first
boot fails with `composefs: verity mismatch`, install onto ext4/btrfs rather than
weakening the setting. `[sysroot] readonly = true` keeps the system root
read-only; `/etc` is left persistent (a transient `/etc` would forget SSH
configs, NetworkManager keyfiles, and user preferences). Source:
<https://github.com/containers/composefs>.

## Image signing (supply chain)

Because the entire OS — desktop, GPU stack, and the local agent plane — ships in
one OCI image, signing that image is what makes every box that pulls the ref
trustworthy. CI signs every push with cosign keyless via GitHub Actions OIDC
(`.github/workflows/mios-ci.yml`). Verify before deploying:

```bash
cosign verify \
  --certificate-identity-regexp="https://github.com/mios-dev/mios" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" \
  ghcr.io/mios-dev/mios:latest
```

This pairs with the boot-time integrity from composefs/verity above: cosign
attests *who built and pushed* the image at the OCI layer; fsverity attests that
*the files haven't changed* once it's deployed.

## How this protects the agent plane

The local agentic AI stack runs entirely *inside* these boundaries rather than
outside them:

- Every Quadlet that hosts an agent service (`mios-agent-pipe`, `mios-llm-light`,
  `mios-pgvector`, `mios-open-webui`, `mios-searxng`, …) runs **unprivileged**
  with `User=` / `Group=` / `Delegate=yes` (Architectural Law 6). The documented
  exceptions are `mios-ceph`, `mios-k3s`, and `mios-forgejo-runner` (rationale in
  their unit headers).
- Those container images are bound into the image at build time (Architectural
  Law 3), so the agent plane is covered by the same cosign + fsverity chain as
  the rest of the OS — there are no pip-installed daemons outside the verified
  root.
- The whole AI surface is reachable only through the single OpenAI-compatible
  endpoint behind `MIOS_AI_ENDPOINT` (Architectural Law 5); the firewall keeps
  the inference lanes (`mios-llm-light` on the `llm_light` port, the gated heavy lanes) and
  the PostgreSQL+pgvector datastore (`:5432`) on loopback / trusted interfaces
  rather than exposing them on the `drop` default zone.
- Code the agent runs on the operator's behalf is subject to fapolicyd execution
  control and SELinux enforcement just like any other process.

## Override surfaces

| Subsystem | Override path |
|---|---|
| Kernel kargs | `bootc kargs edit`, or higher-priority `usr/lib/bootc/kargs.d/*.toml` |
| Sysctl | `/etc/sysctl.d/` |
| SELinux | `setenforce 0` (transient), `/etc/selinux/config` (persistent) |
| Firewall | `firewall-cmd --add-service=...`, `--add-port=...` |
| CrowdSec | `sudo cscli decisions delete --all` |
| fapolicyd | `/etc/fapolicyd/fapolicyd.trust` |
| USBGuard | `/etc/usbguard/rules.conf`, `usbguard allow-device` |

## Reporting vulnerabilities

GitHub private vulnerability reporting on this repo (Security tab → Report a
vulnerability). Do not file public issues for sensitive disclosures.
