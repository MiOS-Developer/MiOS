<!-- AI-hint: Deploy the universal MiOS image as a Hyper-V Gen 2 VM booting a .vhdx on M: cut by bootc install/BIB, because the installer factory-populates /var + /var/home that a raw podman-export skips — read before building a run-off-M: deployment or touching the Ceph/VM bootstrap gates. -->
<!-- AI-related: Justfile vhdx target, src/mios-rs/mios-build/src/artifacts.rs, usr/lib/bootc/kargs.d/10-mios-console.toml, usr/lib/systemd/system/var-home.mount, usr/lib/systemd/system/ceph-bootstrap.service, usr/lib/systemd/system/mios-ceph-bootstrap.service, usr/libexec/mios/ceph-bootstrap.sh, usr/share/mios/mios.toml [storage.cephfs] -->
---
adr: 0005
title: "Sovereign run-off-M: Hyper-V VHDX deployment"
status: accepted
date: 2026-07-12
deciders: [operator, ai-pair]
tags: [deployment, hyper-v, vhdx, bootc-install, ceph, gpu, sovereignty, windows]
laws: [2, 12]
ssot_keys: [storage.cephfs, storage.cephfs.enable]
related_ws: [WS-MDRIVE]
supersedes: []
superseded_by: []
---

# ADR-0005: Sovereign run-off-M: Hyper-V VHDX deployment

## Status
Accepted — 2026-07-12. Architecture accepted; the code changes (Ceph-in-VM
un-gating, OSD-on-M:, the deploy script) are PLANNED (see Implementation). The
`just vhdx` artifact path and the local-fallback storage behavior are DONE.

## Context

MiOS is one universal, immutable bootc/OCI Fedora image that must deploy anywhere
fully-featured, for sovereignty — your data and models stay on your hardware. A
concrete sovereignty target: run the flagship image **directly off the `M:` drive**
of a Windows 11 Pro workstation (Ryzen 9 9950X3D, RDNA2 iGPU + a discrete NVIDIA
GPU), with the OS and its sovereign home living in files on `M:\MiOS-images\`.

The operator's actual failure that forced a decision: a raw
`podman export → wsl --import` (and even `wsl --import-in-place`) of the image
**deadlocks on boot**. Root cause is Architectural **Law 2 (NO-MKDIR-IN-VAR)**: a
bootc image writes **nothing** into `/var` at build time — `/var` content is
*declared* via `usr/lib/tmpfiles.d/*.conf` and *materialized at install/first
boot*. So a bare-rootfs snapshot has:

- an empty `/var` — no `/var/home`, no `/var/lib/containers`, no `/var/roothome`;
- the full set of `bootc-*`/`ostree-*`/composefs host units that expect an
  ostree/bootc deployment substrate a bare rootfs does not have → they block and
  boot deadlocks;
- no GPT, no ESP, no `/boot`, no bootloader.

`wsl --import-in-place` only avoids the tar re-extraction; it does **nothing** for
`/var` factory-population, still runs the Microsoft kernel (bypassing the
bootloader and `usr/lib/bootc/kargs.d`), and has no `/boot`, no composefs `/usr`,
no `bootc upgrade`/`rollback`. It is a userspace preview, not a sovereign OS.
QEMU-WHPX boots a real bootc disk but has **no PCI passthrough** on Windows — the
discrete GPU can't reach the guest, collapsing the heavy vLLM lane to CPU, which
disqualifies it for an AI OS whose reason to exist is the hardware-accelerated
local AI plane.

## Decision

**Deploy the universal image as a Hyper-V Generation 2 VM booting a `.vhdx` on
`M:\MiOS-images\`, cut from the OCI image by `bootc install` / bootc-image-builder
(`just vhdx`).** This is the only candidate that is simultaneously a *true bootc
host* and a *single file that runs off M: in place*.

- **The `/var` fix — run the bootc *installer*, not a filesystem copy.**
  `bootc install to-disk`/`to-filesystem` (and BIB, which wraps the identical
  install logic in a privileged container) partitions the disk (GPT: ESP + boot +
  ext4 root [+ `/var/home`]), writes the ostree/composefs deployment, installs the
  bootloader **with the image's kargs**, and runs the first-boot `/var` factory-
  population so `/var/home`, `/var/lib/containers`, `/var/roothome` exist and the
  host units find their substrate. MiOS has already boot-verified this path
  (`automation/01-system-files-overlay.sh:187` records a `bootc install to-disk`
  boot-verify of the Day-0 image; the same script symlinks `/home → /var/home`).
- **The artifact:** `just vhdx` runs BIB `--type vhd --rootfs ext4` then
  `qemu-img convert -f vpc -O vhdx` (BIB emits VPC `.vhd`; Hyper-V Gen 2 needs
  `.vhdx`). Output is a **dynamically-expanding VHDX** — a single GPT/UEFI-bootable
  file that consumes only used blocks. Drop it at
  `M:\MiOS-images\mios-<version>.vhdx`; Hyper-V attaches it by path and it runs off
  M: in place (not copied to a system location).
- **Boot it as a Gen 2 VM with Secure Boot on the Microsoft UEFI CA template**
  (`Set-VMFirmware -SecureBootTemplate "MicrosoftUEFICertificateAuthority"` — NOT
  "Microsoft Windows"). The baked `plymouth.enable=0` karg
  (`usr/lib/bootc/kargs.d/10-mios-console.toml`) keeps the Hyper-V console visible.
- **Networking:** Hyper-V does not auto-forward localhost; bridge the front door
  with `netsh interface portproxy add v4tov4 listenaddress=127.0.0.1
  listenport=${MIOS_PORT_AGENT_PIPE} connectaddress=<guest-ip>
  connectport=${MIOS_PORT_AGENT_PIPE}` so `$MIOS_AI_ENDPOINT` (the OpenAI front
  door, ADR-0006) reaches the guest.
- **Sovereign storage on M::** attach a **second** dynamic `.vhdx` on M: as a
  single-node Ceph OSD block device (appears in-guest as `/dev/sdb`).
  `/var/home` and `/var/lib/containers` are already CephFS mounts
  (`var-home.mount`/`var-lib-containers.mount`: `Type=ceph`,
  `What=mios@.cephfs=/home`/`=/containers`, `nofail`, gated by
  `ConditionPathExists=/etc/ceph/ceph.conf`). So the sovereign home physically
  persists in `M:\MiOS-images\mios-ceph-osd.vhdx`, decoupled from the root vhdx —
  you can `bootc switch`/rebuild the root vhdx and re-attach the OSD vhdx with home
  + container data intact. A single-node `cephadm bootstrap --single-host-defaults`
  (replication=1) backs it.
- **Local fallback (already correct, no new code):** because both mount units are
  `nofail` **and** `ConditionPathExists`-gated, when Ceph is disabled or not yet
  bootstrapped the mounts are silently skipped and `/var/home` lives on the local
  **20 GiB ext4 `/var/home` partition** `vhdx.toml` already carves. The system
  boots fully with no Ceph; sovereign CephFS is opt-in.
- **dGPU access, ranked:** **DDA (full device) recommended** — the 9950X3D's
  RDNA2 iGPU carries the Windows desktop, so hand the *entire* discrete GPU to MiOS
  (`Dismount-VMHostAssignableDevice` + `Add-VMAssignableDevice`); native CUDA, full
  VRAM. **GPU-P (partition, shared) fallback** — `Add-VMGpuPartitionAdapter`; the
  guest sees a paravirtual GPU via `/dev/dxg` (CUDA-on-WSL user-mode libs), lower
  ceiling but no display eviction.
- **WSL2 `--import-in-place` = explicit disposable preview only** — the 20-minute
  "first light" userspace shim, never the sovereign target.

## Rationale

- **Law 2 (NO-MKDIR-IN-VAR) is precisely why the raw path fails and the installer
  path works.** `/var` is declared, not baked; only the installer materializes it.
  This ADR is the deployment-side consequence of Law 2.
- **Law 12 (BAKE-NOT-FETCH) + degrade-open.** The image ships offline-complete;
  the Ceph/CephFS sovereign layer degrades open to local ext4 when absent, so first
  boot never blocks on storage bring-up.
- **Only Hyper-V Gen 2 is simultaneously** (a) a true bootc host (real UEFI/GPT,
  composefs `/usr`, factory-populated `/var`+`/var/home`, honored kargs, working
  `bootc upgrade`/`rollback`); (b) a single file that runs off M: with no
  import/extract; (c) native to Windows 11 Pro with no QEMU install; (d) able to
  feed the heavy lanes the real dGPU via DDA/GPU-P.
- **Precedent:** `bootc install to-disk`/`to-filesystem` (boot-verified in-tree);
  bootc-image-builder `--type vhd|raw|qcow2`; Hyper-V Gen 2 Secure Boot with the
  Microsoft UEFI CA template; `cephadm bootstrap --single-host-defaults` with a
  BlueStore OSD on a block device; CephFS `Type=ceph` `nofail`+`ConditionPathExists`
  mounts. The ucore-hci base already ships MOK-signed NVIDIA akmods + CUDA inside
  the image, so the in-guest driver is present.

## Alternatives considered

- **`podman export → wsl --import` (raw rootfs).** Rejected: empty `/var`, no
  `/var/home`, bootc-host units deadlock, no bootloader/ESP. The originating failure.
- **`wsl --import-in-place`.** Rejected as the *primary* target: fastest login and
  free `/dev/dxg` GPU, but still an unpopulated `/var`, MS kernel, no bootloader, no
  `bootc upgrade`/`rollback`. Kept as an explicit disposable preview only.
- **QEMU-WHPX (raw/qcow2).** Rejected: no PCI passthrough on Windows → dGPU
  unreachable → heavy lane collapses to CPU. Keep only as a throwaway headless
  smoke-test.
- **Ceph OSD on a raw NTFS folder / no Ceph.** The local 20 GiB ext4 `/var/home`
  fallback covers the no-Ceph case (and is the recommended first-light default);
  the OSD-on-M:-vhdx is what makes home *sovereign and rebuild-surviving* when the
  operator opts in.

## Consequences

Positive:
- A true, upgradable/rollback-able MiOS running off M: with the real dGPU and a
  sovereign home whose bytes live in a file on M:.
- Root vhdx and sovereign-home OSD vhdx are decoupled — rebuild the OS without
  losing home/containers.
- Local-ext4 fallback needs no new code; sovereign storage is opt-in.

Negative / honest costs:
- **The one blocker:** both `usr/lib/systemd/system/ceph-bootstrap.service` and
  `mios-ceph-bootstrap.service` carry `ConditionVirtualization=no` — they refuse to
  run inside a VM. Since the Hyper-V guest *is* a VM, single-node Ceph-on-M: never
  fires as shipped. This must be relaxed to a **config gate** (prefer a
  `[storage.cephfs].enable`-driven flag-file over blanket removal, so transient CI
  VMs don't auto-enable Ceph).
- Cutting the vhdx requires a **Linux podman** (BIB reads
  `/var/lib/containers/storage`); the removed MiOS-DEV machine must be
  re-established once.
- Client-Hyper-V DDA is unofficial-but-works; and DDA evicts the dGPU from Windows
  (acceptable here because the iGPU drives the desktop).
- M: is NTFS, not a ReFS Dev Drive — VHDX-on-NTFS is fine but forgoes ReFS
  block-cloning for fast checkpoint/rebuild.

Status — **DONE:** the `just vhdx` artifact path (`config/artifacts/vhdx.toml`
creates the `mios` user, blacklists nouveau, sets `iommu=pt`, carves 150 GiB `/` +
a 20 GiB `/var/home` fallback); the `plymouth.enable=0` console karg; the CephFS
`nofail`+`ConditionPathExists` local-fallback behavior. **PLANNED (WS-MDRIVE):** the
VM-un-gate of Ceph, the OSD-on-`/dev/sdb` wiring, the SSOT flip to
`[storage.cephfs].enable=true`, and the Windows deploy script.

## Implementation

C:\MiOS (image):
- `usr/lib/systemd/system/ceph-bootstrap.service` + `mios-ceph-bootstrap.service` —
  replace `ConditionVirtualization=no` with a config gate: drop the line and add
  `ConditionPathExists=/run/mios/ceph-enabled` (written by a tiny `ExecStartPre`
  that reads `[storage.cephfs].enable` from `mios.toml`), or rely on the existing
  `!/var/lib/ceph/.bootstrapped` sentinel + `[storage.cephfs].enable=false` default.
- `usr/libexec/mios/ceph-bootstrap.sh` — after `cephadm bootstrap`, add the OSD +
  fs/pool creation on the second disk (`ceph orch daemon add osd
  $(hostname):/dev/sdb`), guarded to no-op if the disk is absent; keep the 4 GiB MDS
  cache cap.
- `usr/share/mios/mios.toml [storage.cephfs] enable = true` — flip only after the
  OSD vhdx is attached; leave `false` for local-fallback first boot.
- No console/kargs change needed — `usr/lib/bootc/kargs.d/10-mios-console.toml`
  (`plymouth.enable=0`) and `iommu=pt` (in `vhdx.toml`) already cover Hyper-V.

C:\MiOS\Justfile + config:
- Add a `vhdx-m` convenience recipe (after the `vhdx:` target) that runs `just vhdx`
  then copies `output/*.vhdx` → `M:\MiOS-images\mios-<VERSION>.vhdx` and prints the
  `New-VM` one-liner. `config/artifacts/vhdx.toml` needs no change (optionally bump
  root to 200 GiB given M: headroom).

C:\mios-bootstrap (Windows deploy — sibling repo, not this image):
- `deploy-mios-hyperv-m.ps1` (new): (a) `podman machine`-load the image tar + run
  `just vhdx` if the vhdx is missing; (b) `New-VM -Generation 2` off the M: vhdx;
  (c) `Set-VMFirmware -SecureBootTemplate MicrosoftUEFICertificateAuthority`;
  (d) create + attach `mios-ceph-osd.vhdx`;
  (e) `netsh portproxy` on the `agent_pipe` port;
  (f) optional `Add-VMGpuPartitionAdapter` or the DDA block.

Fastest path to first boot: re-establish a Linux podman once → load the image tar
→ `just vhdx` onto M: → `New-VM -Generation 2` + Microsoft-UEFI-CA + `Start-VM` →
`netsh portproxy` on the `agent_pipe` port →
`curl http://localhost:${MIOS_PORT_AGENT_PIPE}/v1/models` from Windows. Then
harden: DDA/GPU-P, then sovereign Ceph-on-M:, then confirm `bootc upgrade`/`rollback`.

## References

- bootc `install to-disk` / `to-filesystem`: <https://bootc.dev/bootc/install.html>
  (boot-verified in-tree at `automation/01-system-files-overlay.sh:187`).
- bootc-image-builder (`--type vhd|raw|qcow2`): <https://github.com/osbuild/bootc-image-builder>
- Hyper-V Gen 2 Secure Boot templates (Microsoft UEFI CA):
  <https://learn.microsoft.com/windows-server/virtualization/hyper-v/learn-more/generation-2-virtual-machine-security-settings-for-hyper-v>
- Hyper-V DDA (`Dismount-VMHostAssignableDevice` + `Add-VMAssignableDevice`):
  <https://learn.microsoft.com/windows-server/virtualization/hyper-v/plan/plan-for-deploying-devices-using-discrete-device-assignment>
- Hyper-V GPU-P (`Add-VMGpuPartitionAdapter`) + CUDA-on-WSL `/dev/dxg` model.
- `cephadm bootstrap --single-host-defaults` + BlueStore OSD on a block device:
  <https://docs.ceph.com/en/latest/cephadm/install/>
- `wsl --import-in-place` + WSLg `/dev/dxg`: <https://learn.microsoft.com/windows/wsl/>
- MiOS memory: "release topology" (the image these artifacts are cut from), Day-0
  build GREEN (the Day-0 image boot-verify).
- Sibling ADRs: ADR-0001 (the universal image + Law 2 background), ADR-0006 (the
  OpenAI front door on the `agent_pipe` port that the portproxy targets).
