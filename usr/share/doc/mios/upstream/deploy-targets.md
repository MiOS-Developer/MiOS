<!-- AI-hint: Documentation of MiOS deployment-target methods (bootc host, Hyper-V Gen2, QEMU/KVM, WSL2, Anaconda ISO, RAW disk), detailing the Justfile build recipes and per-artifact config requirements that turn the single OCI image into a bootable system; use to guide automated deployment workflows.
     AI-related: mios-dev, usr/lib/bootc/kargs.d/10-mios-console.toml, config/artifacts -->
# Deployment Targets — bootc / Hyper-V / WSL2 / QEMU / ISO / RAW

> **Purpose.** MiOS is one thing built two ways at once: an immutable
> bootc/OCI-shaped Fedora workstation *and* a local, self-replicating agentic
> AI operating system. Both halves ship inside a single OCI image
> (`ghcr.io/mios-dev/mios:latest`). This doc is about the *last mile* — how that
> one image becomes a running machine on whatever substrate you have: a
> bootc-managed bare-metal host, a Hyper-V Gen 2 VM, QEMU/KVM, a WSL2 distro, an
> installer ISO, or a RAW disk. Pick the target that fits; the system you get is
> the same image either way.
>
> Where this sits in the lifecycle: **build pipeline → OCI image → disk
> artifact → boot → bootc Day-2 lifecycle.** The build pipeline (`just build`)
> produces the image; `bootc-image-builder` (BIB) cuts disk artifacts from it
> (see `upstream/bib.md`); the targets below boot one of those; and from then on
> `bootc upgrade` / `bootc rollback` carry the host forward like `git pull` /
> Ctrl-Z. The same artifact that brings up GNOME/Wayland also brings up the
> local inference lanes (`mios-llm-light` on the `llm_light` port and the gated heavy GPU
> lanes), the agent-pipe/Hermes orchestration, and the PostgreSQL+pgvector
> memory — there is no separate "AI install" step.
>
> Source: `usr/share/doc/mios/guides/deploy.md`, `Justfile` (recipes `raw`,
> `iso`, `qcow2`, `vhdx`, `wsl2`), `config/artifacts/*.toml`.

## Bootc-managed Fedora host (preferred)

The native target. No disk artifact needed — a Fedora-bootc-compatible host
pulls the OCI image directly and switches its own root onto it.

```bash
sudo bootc switch ghcr.io/mios-dev/mios:latest && sudo systemctl reboot

# Day-2
sudo bootc upgrade && sudo systemctl reboot
sudo bootc switch <ref>     # change tag
sudo bootc rollback         # undo last upgrade
```

This is the form every other target converges to: once any of the artifacts
below boots, the deployed system is bootc-managed and upgrades/rolls back the
same way.

## Hyper-V Gen 2 (Windows)

The VHDX is never published: GitHub release assets stop at 2 GiB a file and
GHCR at 10 GB a layer. Build it from the published image digest on MiOS-DEV or
any rootful podman host (`[deploy.formats.vhdx]`):

```bash
export MIOS_SSH_PUBKEY="$(cat ~/.ssh/id_ed25519.pub)"     # and/or MIOS_USER_PASSWORD_HASH
sudo -E miosd artifact-build vhdx --image ghcr.io/mios-dev/mios@sha256:<digest> \
    --output /mnt/m/MiOS/artifacts/vhdx
# -> disk.vhdx, a dynamic VHDX (bootc-image-builder's VHD, converted); without
#    --output it lands in build/vhdx ([build.artifacts].output_dir)
miosd artifact-boot-test vhdx --disk /mnt/m/MiOS/artifacts/vhdx/disk.vhdx \
    --identity ~/.ssh/id_ed25519
```

With no credential the build stops: there is no default password (see
[Password hash & SSH key](#password-hash--ssh-key)). `just vhdx` runs the same
command against the local build.

Then on the Windows host, with the values `[deploy.formats.vhdx.vm]` declares
(copy the disk first; a VM writes to the file it boots):

```powershell
$disk = 'M:\MiOS\vms\MiOS.vhdx'
Copy-Item 'M:\MiOS\artifacts\vhdx\disk.vhdx' $disk
New-VM -Name MiOS -Generation 2 -MemoryStartupBytes 8GB -VHDPath $disk -SwitchName 'Default Switch'
Set-VMMemory -VMName MiOS -DynamicMemoryEnabled $false
Set-VMProcessor -VMName MiOS -Count 4
Set-VMFirmware -VMName MiOS -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority
Start-VM -Name MiOS
```

- **Generation 2** only: the disk is UEFI and GPT.
- **Secure Boot with the Microsoft UEFI CA** template, not "Microsoft Windows":
  the Windows template rejects the Linux shim.
- The first-boot console fix is already baked: `plymouth.enable=0` ships in
  `usr/lib/bootc/kargs.d/10-mios-console.toml`, because Plymouth otherwise
  steals the framebuffer and makes Hyper-V/QEMU/serial boot invisible. (Console
  verbosity kargs are in `00-mios.toml` + `10-mios-verbose.toml`.) The disk
  carries the image's own `kargs.d`; the build restates none.

## QEMU/KVM

```bash
just qcow2   # requires MIOS_USER_PASSWORD_HASH and MIOS_SSH_PUBKEY
qemu-system-x86_64 -enable-kvm -m 16G -smp 8 \
  -drive file=output/*.qcow2,if=virtio \
  -bios /usr/share/edk2/ovmf/OVMF_CODE.fd \
  -nic user,model=virtio
```

For libvirt: `virt-install --import --osinfo fedora-bootc --disk path=output/*.qcow2 ...`

This is the quickest local round-trip for validating an image — the same qcow2
is what MiOS itself uses for its VFIO/Looking-Glass virtualization story, so a
QEMU boot exercises the immutable host end-to-end before you commit it to metal.

## WSL2

```powershell
just wsl2   # on a Linux build host -> output/wsl2/mios-rootfs.tar.gz
wsl --import MiOS C:\WSL\MiOS .\output\wsl2\mios-rootfs.tar.gz
wsl -d MiOS
```

BIB has no native `wsl2` type, so the `wsl2` recipe exports the OCI image's
rootfs directly (`podman create` + `podman export | gzip`) into
`output/wsl2/mios-rootfs.tar.gz` for `wsl --import`.

WSL2 caveats:

- The Windows-hosted kernel ignores the image's `kargs.d` (Hyper-V owns the
  kernel), so kernel-side tweaks like the Plymouth/console fix above are moot
  here.
- Set `systemd=true` in the imported instance's `/etc/wsl.conf` (MiOS ships
  this already) so the agent stack's systemd units start.
- bootc commands work inside the distro, but `bootc switch` requires writing the
  new rootfs back into the WSL distribution — so the bootstrap installer
  re-imports a fresh rootfs rather than doing a `bootc switch`-in-place.

The WSL2 path is the primary target the Windows bootstrap (`Get-MiOS.ps1`) drops
the build into, alongside a VHDX, ISO, and qcow2 — pick whichever fits the host.

## Anaconda installer ISO

```bash
just iso
```

Boot the resulting `output/*.iso` (USB or physical media), run the Anaconda
installer, reboot. On first boot the deployed system runs `bootc upgrade` to
align with the remote `:latest` tag, so the installed host immediately tracks
the published image.

> [!WARNING]
> **Bare-Metal & USB Deployment Status Notice (task_2457)**:
> Automated USB/Ventoy installer media generation is functional for WinPE live sessions and standard Fedora recovery, but automated bare-metal deployment of the MiOS bootc image is disconnected: the kickstart scripts deploy standard Fedora rather than installing MiOS.
> From a live environment with network access, install with `mios-install disk --target-disk DEV --yes` (or `--auto-select`), which runs the image's own `bootc install to-disk` in podman (ADR-0014; `upstream/bootc.md` §Installing a bootc image).
> Offline, `tools/install.sh --target-disk DEV` installs the staged `oci-archive:` from the MiOS-Repo partition through `mios-install disk --source oci-archive:PATH`, which sets `--target-imgref` so the installed host upgrades from the published image.

> The `iso` recipe mounts **only** `config/artifacts/iso.toml`; mounting a
> second BIB config crashes BIB with `found config.json and also config.toml`.
> Details in `upstream/bib.md`.

## RAW

`just raw` produces an 80 GiB ext4 RAW disk image (from
`config/artifacts/bib.toml`). Useful for:

- `dd if=output/*.raw of=/dev/sdX` to a physical USB or disk
- Flashing to an SBC or appliance
- Cloud import (most clouds accept RAW + a custom kernel)

## Password hash & SSH key

A disk has no interactive installer to create a login, so the build gives the
`[identity].username` account (in `[identity].groups`) the operator's
credential. Nothing is committed: `miosd artifact-build` renders
the builder config per build into a private temporary file, from the names
`[deploy.identity]` gives, and refuses to build when it finds neither half:

- `MIOS_USER_PASSWORD_HASH` (from `openssl passwd -6`), the console login, or
  `[auth].password_hash` under `password_policy = "hashed"` in your layered
  `mios.toml`.
- `MIOS_SSH_PUBKEY` (an OpenSSH public key; SSH takes keys only), or
  `[auth].existing_ssh_key` (the key or its `.pub` file) under
  `ssh_key_action = "existing"`.

The vendor `[auth].password` is never used. `mios-gate artifact-recipes`
fails if any recipe under `config/artifacts/` carries a placeholder or a
credential. The `raw` and `wsl2` recipes do not take these.

## Cross-refs

- `usr/share/doc/mios/upstream/bib.md` — bootc-image-builder: output types, the
  ISO config gotcha, and the VHDX `.vhd`→`.vhdx` conversion idiom.
- `usr/share/doc/mios/guides/deploy.md` — bootc + Day-2 lifecycle in depth.
- `usr/share/doc/mios/guides/security.md` — hardening kargs and posture (FIPS,
  VFIO, lockdown), companion to the console kargs referenced above.
- `Justfile` — the source of truth for the build/artifact recipes.
