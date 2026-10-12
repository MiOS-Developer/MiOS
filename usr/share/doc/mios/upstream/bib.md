<!-- AI-hint: Documentation for the bootc-image-builder (BIB) tool, detailing how MiOS uses it to transform the localhost/mios:latest OCI image into deployable disk artifacts (raw, anaconda-iso, qcow2, vhd→vhdx, wsl2) via Justfile recipes and config/artifacts/*.toml configurations. BIB is the last stage of the MiOS build pipeline before the bootc deploy/upgrade/rollback lifecycle. -->
# bootc-image-builder (BIB)

## Purpose — where BIB sits in the whole

MiOS is one OS built two ways at once: an **immutable, bootc/OCI-shaped Fedora
workstation** — the entire system is a single container image you boot,
`bootc upgrade` like a `git pull`, and `bootc rollback` like a Ctrl-Z — that is
*also* a local, self-replicating, agentic AI operating system. The MiOS build
pipeline (`Containerfile` + numbered `automation/NN-*.sh` scripts) assembles that
one image, `localhost/mios:latest`, ending in `bootc container lint`
(Architectural Law 4).

**bootc-image-builder is the bridge from that image to bootable media.** An OCI
image is the canonical artifact and the upgrade unit, but you cannot put an OCI
image directly onto bare metal, a hypervisor, or WSL — BIB converts the *same
already-built image* into installable/bootable disk artifacts (`raw`, installer
`iso`, `qcow2`, Hyper-V `vhd`→`vhdx`) under `output/`. After the artifact boots
once, the host is on the bootc lifecycle and pulls subsequent updates as OCI
images directly — so BIB matters most for **first install**, while
`bootc upgrade`/`rollback` carry the system forward. Because BIB reads the
already-built image, the image-defining Architectural Laws (deterministic,
self-contained, bound images) hold automatically in every artifact it cuts; the
local agent stack and all the OS capabilities ship inside the same image, not
bolted on per-target.

This doc is the operator/build-author reference for the BIB tool itself: which
output types MiOS produces, the recipes that produce them, the TOML schema, and
the gotchas that bit us.

> Image: `quay.io/centos-bootc/bootc-image-builder:latest`
> (`Justfile:34`, `MIOS_IMG_BIB`; overridable via `MIOS_BIB_IMAGE`).
> Used by MiOS to convert `localhost/mios:latest` (the OCI image built by
> `just build`) into deployable disk artifacts under `output/`.
> Source: `Justfile`, `usr/share/doc/mios/guides/deploy.md`,
> `config/artifacts/{bib,iso,wsl2}.toml`, `src/mios-rs/mios-build/src/artifacts.rs`.

## Project

- Repo (archived): <https://github.com/osbuild/bootc-image-builder> — the
  upstream repository has been merged into and superseded by
  <https://github.com/osbuild/image-builder>; the standalone repo is read-only.
- Docs: <https://osbuild.org/docs/bootc/>
- Image: the osbuild bootc docs still publish and reference
  `quay.io/centos-bootc/bootc-image-builder:latest` as the canonical container to
  run, so MiOS keeps that pull reference (SSOT `[image].bib`, `Justfile`
  `MIOS_IMG_BIB`). Migrate to the `image-builder` successor only once it ships a
  bootc-container disk-image path that can replace the `build --type` invocations
  MiOS depends on — `image-builder-cli` is package-input oriented (not a
  bootc-container drop-in) and is itself now folded into `image-builder`.

## Output types

Every recipe runs `just build` first (the OCI image must exist before any BIB
leg, since BIB reads from `/var/lib/containers/storage`).

| Type | MiOS Justfile recipe | Output location | Notes |
| --- | --- | --- | --- |
| `raw` | `just raw` | `output/*.raw` | ext4 root; bootable disk image |
| `anaconda-iso` | `just iso` (`miosd artifact-build iso`) | `build/iso/` | account and credential rendered into the kickstart |
| `qcow2` | `just qcow2` (`miosd artifact-build qcow2`) | `build/qcow2/` | needs an operator credential; none is defaulted |
| `vhd` | `just vhdx` (`miosd artifact-build vhdx`) | `build/vhdx/disk.vhdx` | BIB emits VPC `.vhd`; converted to a dynamic `.vhdx` |
| `wsl2` | `just wsl2` | `output/wsl2/mios-rootfs.tar.gz` | **not a BIB type** — `podman export` of the rootfs for `wsl --import` |
| `vmdk` | (not currently in Justfile) | — | available |
| `gce` | (not currently in Justfile) | — | available |
| `ami` | (not currently in Justfile) | — | available |

> WSL2 is the one target BIB does not produce. `just wsl2` exports the image
> rootfs (`podman create` → `podman export | gzip`) for `wsl --import`, because
> BIB has no `--type wsl2`. Listed here so the full deploy-target matrix lives in
> one place.

## Critical: ISO recipe gotcha

The `iso` recipe **only mounts `iso.toml`**. Mounting both `bib.toml`
and `iso.toml` causes BIB to crash with:

```
found config.json and also config.toml
```

This is the `Justfile:iso` v0.2.0 fix. If you author a new BIB type, mount
exactly one config TOML.

## TOML schema (high-level)

The ISO's committed recipe is `config/artifacts/iso.toml`. It pins the root
filesystem size, blacklists `nouveau` at install time, and carries the
kickstart. Because BIB issue #528 makes `[customizations.user]` ignored when a
kickstart is present, the account belongs *inside* the kickstart -- and
`miosd artifact-build iso` appends it there per build (`user`, `sshkey`), from
`[identity]` and the operator's credential. The committed recipe holds neither:

```toml
# config/artifacts/iso.toml -- abridged
[customizations.kernel]
append = "rd.driver.blacklist=nouveau modprobe.blacklist=nouveau iommu=pt"

[[customizations.filesystem]]
mountpoint = "/"
minsize    = "150 GiB"

[customizations.installer.modules]
disable = ["org.fedoraproject.Anaconda.Modules.Users"]   # user created in kickstart

[customizations.installer.kickstart]
contents = """
text --non-interactive
zerombr
clearpart --all --initlabel --disklabel=gpt
reqpart --add-boot
part / --grow --fstype ext4
network --bootproto=dhcp --device=link --activate --onboot=on
reboot --eject
"""
```

The disk formats (`qcow2`, `vhdx`) have no committed recipe at all: the whole
config is rendered from the SSOT -- `[[customizations.user]]` from `[identity]`
plus the credential, and the `/` floor from `[bootc_install].root_min_gb`.
Kernel arguments are not restated: bootc installs the image's own
`usr/lib/bootc/kargs.d`.

Mutually exclusive sections:

- `[customizations.user]` ⊻ `[customizations.installer.kickstart]`
  (BIB #528: the kickstart wins; define the user there)
- (other top-level sections coexist freely)

## VHDX conversion (`miosd artifact-build vhdx`)

BIB writes Hyper-V disks as VPC (`--type vhd`, `[deploy.formats.vhdx].bib_type`);
Hyper-V Gen 2 wants `.vhdx`. The native builder converts with the `qemu-img`
the BIB image itself carries, so the host needs nothing but rootful podman:

```bash
podman run --rm -v OUT:/output --entrypoint qemu-img ${BIB} \
    convert -p -O vhdx -o subformat=dynamic /output/vpc/disk.vhd /output/disk.vhdx
```

The `.vhd` is removed once the `.vhdx` exists, and a result under
`[deploy.verify].min_bytes` fails the build.

## Credentials

A disk's credential is rendered per build into a 0600 temporary config and
never committed. `miosd artifact-build` reads the names `[deploy.identity]`
gives -- `MIOS_USER_PASSWORD_HASH` (`openssl passwd -6`) and `MIOS_SSH_PUBKEY`
-- then `[auth]` in the layered `mios.toml`, and refuses to build when it finds
neither; there is no default password. `mios-gate artifact-recipes` fails on a
`REPLACE` placeholder or a committed credential in any recipe.

## Gotchas: what the published image hands BIB

Four properties of the image itself stop every BIB disk build of it before a
disk is written. Measured on MiOS-DEV against `ghcr.io/mios-dev/mios:latest`
with `miosd artifact-build vhdx`, clearing each in a throwaway derived image to
reach the next:

- **The CoreOS `disk.yaml`.** MiOS's base is uCore, which is Fedora CoreOS,
  and ships CoreOS's `/usr/lib/image-builder/bootc/disk.yaml`. Its root
  partition carries `mkfs_options: { agcount: 2 }`, which the 2026-06-18
  `bootc-image-builder:latest` cannot parse (`json: unknown field
  "agcount"`). That file also declares an xfs root, which wins over
  `--rootfs` (the manifest formats `/` as xfs) while
  `[bootc_install].root_fs_type` is ext4.
- **`VERSION_ID="0.3.0"`.** BIB names the distro `bootc-<ID>-<VERSION_ID>` and
  accepts at most one dot in the version (`too many dots in the version (2)`).
  MiOS's `os-release` puts its own release there; upstream's is `44`, and
  `IMAGE_VERSION` already carries MiOS's.
- **The baked bound-image store.** With `MIOS_BAKE_BOUND_IMAGES=1` the image
  carries a container store under `/usr/lib/containers/storage`. BIB's
  `org.osbuild.container-deploy` copies the mounted image with `cp -a`, which
  cannot stat that store's nested overlay entries, so the build-root stage fails.
- **Logically bound images without that store.** `bootc install` resolves every
  `/usr/lib/bootc/bound-images.d` entry from container storage
  (`[bootc_install].bound_images = "stored"`); an image built without the baked
  store fails on the first one (`code.forgejo.org/forgejo/runner:7 does not
  resolve to an image ID`). Bound images pulled on first use (P0-3) remove both
  of the last two.

Past those four, the host matters. `[security].composefs_mode = "verity"`
needs fs-verity on the root filesystem: CoreOS's xfs root does not offer it,
and with an ext4 root made with `verity: true` the MiOS-DEV kernel
(6.18 `microsoft-standard-WSL2`, built without `CONFIG_FS_VERITY`) still
cannot set it, so `bootc install` stops at `Filesystem does not support
fs-verity`. BIB's `--in-vm`, which would bring its own kernel, fails in this
build with `KeyError: 'graphroot'`. A disk of a verity image therefore needs a
host kernel built with `CONFIG_FS_VERITY` -- Fedora's is, so MiOS-DEV on the
Hyper-V provider would be; check a CI runner's before relying on it.

## Cross-refs

- `usr/share/doc/mios/guides/deploy.md` — operator deploy guide (the full
  artifact → install → first-boot path)
- `usr/share/doc/mios/upstream/deploy-targets.md` — per-target matrix
- `Justfile` — `raw` / `iso` / `qcow2` / `vhdx` / `wsl2` / `all` recipes
