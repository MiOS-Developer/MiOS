<!-- AI-hint: Documentation for the MiOS self-build lifecycle, detailing the build chain, CI/CD workflows, and local build modes (Bootstrap, CI/CD, Windows, Linux/Justfile, in-place self-build, Ignition appliance) for generating the MiOS OCI image and the disk images cut from it.
     AI-related: mios-dev, mios-bootstrap, mios-ci, mios-build-local, mios-builder, mios-build-driver, mios-installer -->
# Self-build guide

## What this is, and why it matters

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is a single container image — boot it,
`bootc upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is
*also* a **local, self-replicating agentic AI operating system**. The same image
that ships the GNOME/Wayland desktop, GPU wiring (NVIDIA + ROCm + iGPU via CDI),
KVM/libvirt VFIO passthrough, and the k3s + Ceph one-node-cluster path also ships
the full local agent stack — inference lanes, the agent-pipe orchestrator,
MiOS-Hermes, PostgreSQL+pgvector memory — behind one OpenAI-compatible endpoint.

"Self-replicating" is the literal property this guide documents: because the
whole OS is a single rebuildable OCI image, the default package configuration
retains the compiler, source, container and verification tools for producing its
next generation. Disk artifacts use the configured `bootc-image-builder` OCI
image through Podman; a host command with that name is not a prerequisite.
The build pipeline is the front half of the system lifecycle —
**build pipeline → OCI image → bootc lifecycle on the host** — and self-build is
the loop that closes it: `MiOS vN` builds `MiOS vN+1`, then deploys it atomically
and can roll it back. This doc is the operator/builder reference for every way
that loop can be driven.

Source of truth: `Containerfile`, `Justfile`, and
`/usr/share/mios/mios.toml`. `[packages.self-build]` declares image, repository
and verification tools and composes `[packages.build-toolchain]` through
`requires_sections`. `[packages.devcontainer]` consumes the same shared groups.
`/usr/share/doc/mios/reference/PACKAGES.md` explains the package policy; it does
not supply a fallback package catalog. See the deeper build-pipeline
rules in [`engineering.md`](engineering.md) and the deploy/Day-2 side in
[`deploy.md`](deploy.md).

## Build chain

```
'MiOS' vN (running) → podman build → 'MiOS' vN+1 (OCI image)
                                         ↓
                              rechunk → cosign keyless sign → push to GHCR
                                         ↓
                              bootc upgrade → reboot → 'MiOS' vN+1 (running)
```

The image is the deliverable; the disk artifacts (RAW / ISO / qcow2 / VHDX /
WSL2 tarball) are cut *from* that same image by `bootc-image-builder`. Whatever
mode you use, the final `RUN bootc container lint` (Architectural Law 4) must
pass or the build fails — that is what keeps each generation deterministic and
deployable.

## Modes

### Mode 0 -- Bootstrap (initial install, fresh Linux)

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/install.sh)"
```

Runs the bootstrap installer from `mios-bootstrap.git`. On a non-bootc
host it clones this repo and runs `install.sh` to lay down the FHS
overlay (the repo root IS the deployed system root — `usr/`, `etc/`, `srv/`,
`var/` land exactly where you see them); on a bootc-managed Fedora host it runs
`bootc switch ghcr.io/mios-dev/mios:latest`.

### Mode 1 -- CI/CD (recommended for production)

`.github/workflows/mios-ci.yml` builds, rechunks (on tag push), signs
(keyless cosign), and pushes the image on every tag and on `main`.
End users receive updates via the bootc lifecycle:

```bash
sudo bootc upgrade && sudo systemctl reboot
```

### Mode 2 -- Windows local build

Use the [bootstrap entry and installation guide](https://github.com/mios-dev/mios-bootstrap/blob/main/usr/share/doc/mios-bootstrap/guides/bootstrap_install.md).
Windows provisions the host and hands builds to `podman-MiOS-DEV`. The resource
budget comes from the resolved TOML. Operator selections belong in the TOML
overlay before the build starts. `mios-build-local.ps1` is a compatibility
redirector, not a separate builder or resource policy.

### Mode 3 -- Linux local build (Justfile)

```bash
just build           # OCI image (runs bootc container lint as final step)
just rechunk         # Optimized Day-2 deltas
just lint            # Re-run bootc container lint
just raw             # RAW disk image
just iso             # Anaconda installer ISO
just qcow2 / vhdx / wsl2   # Other formats (need MIOS_USER_PASSWORD_HASH)
just sbom            # CycloneDX SBOM
just all-bootstrap   # build + rechunk + log to bootstrap repo
```

`just --list` shows every target; `Justfile` is the source of truth for the
Linux side, `mios-build-local.ps1` is the Windows equivalent.

### Mode 4 -- Self-build (running 'MiOS' builds next 'MiOS')

The development substrate rebuilds the image from its full source checkout.
Run the build in MiOS-DEV:

```bash
git clone https://github.com/mios-dev/mios.git
cd mios
just preflight
just build
just lint
```

`just rechunk` runs the configured containerized rechunk step. Review the build
log, image and selected artifact before using the [deployment lifecycle](deploy.md)
to switch a bootc host. Building an image and deploying it are separate actions.

### Mode 5 -- Ignition appliance

`config/ignition/` holds Butane configs for fully automated builds on a
fresh Fedora CoreOS or Fedora Server instance. Compile `.bu` →
`.ign` with Butane (<https://coreos.github.io/butane/>), provision the
target with the resulting `.ign`. On first boot it installs `git podman
just`, clones 'MiOS', and produces a live installer ISO at
`/usr/src/mios/output/mios-installer.iso`.

## Bootstrapping the first image

The build chain is self-replicating once a 'MiOS' image exists, but the very
first generation is built from the upstream base — no prior 'MiOS' image needed:

1. Install Podman on any Linux (Fedora, Debian/Ubuntu) or use Podman
   Desktop on Windows.
2. Clone the repo and run `podman build` (or `mios-build-local.ps1` on
   Windows).
3. The Containerfile pulls
   `ghcr.io/ublue-os/ucore-hci:stable-nvidia` as the base (the `mios.toml`
   `[image].base` default; `[image].base_no_nvidia` =
   `ghcr.io/ublue-os/ucore-hci:stable` for non-NVIDIA hosts) -- no prior
   'MiOS' image needed.
4. Deploy the result to the target (bare metal via ISO, Hyper-V via
   VHDX, etc.).
5. Subsequent builds can run from inside the deployed 'MiOS' (Mode 4).

## Dependency retention and verification

`[packages.self-build].retain_toolchain = true` is the default. The cleanup
phase preserves the selected dependency closure rather than removing tools
whose package group happens to end in `-build`. Setting retention to false is
an explicit choice to create an image that needs a separate development builder.
Core and development profiles must install the selected self-build dependencies
even when the virtualization phase is absent.

Rust target standard libraries and a matching linker remain prerequisites for
cross compilation; installing Cargo alone does not provision every target.
Use the checked-in toolchain and build configuration inside MiOS-DEV. The shared
agent requirements are installed into `/usr/lib/mios/agents/.venv`; system Python
consumers obtain their dependencies from the declared RPM groups.

Run these read-only checks from a full checkout inside MiOS-DEV:

```bash
command -v podman buildah skopeo bootc git just cargo rustc make cmake
podman info
df -h /var/lib/containers
just preflight
```

Then run the selected image build and its runtime checks. A declared package
list or successful resolver test does not establish that a full deployed image
has built and can rebuild itself. Retain the build log and report missing RPMs,
target libraries, repository access or service prerequisites as failures.

## Rust executable roles and upstream contracts

`[build.native]` in `/usr/share/mios/mios.toml` declares the Cargo workspaces,
platform restrictions and four separate executable categories. These are MiOS
roles, independent of Cargo target kinds and systemd lifecycle settings:

| Category | Primary role | Canonical Linux destination |
|---|---|---|
| `cli` | User commands, generators and verification tools | `/usr/bin` |
| `apps` | User applications, including desktop applications | `/usr/bin` |
| `services` | Programs invoked by service units | `/usr/libexec/mios` |
| `daemons` | Long-running background processes | `/usr/libexec/mios` |

A daemon can also be supervised by a service unit. Its primary executable role
remains `daemons`; the unit's `Type=` specifies startup and readiness behavior.
`Type=exec` detects executable startup failures, `Type=oneshot` waits for the
command to finish, and `Type=notify` requires an implemented readiness protocol.
See the upstream [service-unit contract](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)
and [daemon guidance](https://github.com/systemd/systemd/blob/main/man/daemon.xml).

The Rust `mios-build` library discovers workspace members through
[`cargo metadata --format-version 1 --no-deps`](https://doc.rust-lang.org/stable/cargo/commands/cargo-metadata.html).
Only `bin` targets are installed. Libraries, dependency packages, examples,
tests and artifact sidecars are excluded. Cargo's platform filter describes
dependency resolution; it does not define application or daemon roles.
Every executable must have exactly one SSOT category. Duplicate, missing,
unclassified or stale entries fail before installation.

Inspect the inventory from a full checkout inside MiOS-DEV:

```bash
miosd native-targets --root . --platform linux --json
miosd native-targets --root . --platform windows --json
miosd native-build-settings --root . --arch x86_64 --json
```

Metadata inspection uses `--offline --locked` and requires prepared Cargo
dependency metadata and a current lockfile. Cargo's upstream
[read-only metadata issue](https://github.com/rust-lang/cargo/issues/10096)
documents why querying metadata without lock protection can mutate a lockfile.
The [metadata network regression report](https://github.com/rust-lang/cargo/issues/15272)
also distinguishes full dependency discovery from `--no-deps`. These reports
justify explicit query flags; they do not establish a new defect in MiOS.
The image builder fetches both locked graphs first, then uses the same
`automation/55-native-build.sh` installer to stage the selected Linux artifacts.
The shared installer replaces old symlinks before creating compatibility links,
and image staging prefixes never appear in deployed link targets.
Public and internal paths follow the [Filesystem Hierarchy Standard](https://refspecs.linuxfoundation.org/FHS_3.0/fhs-3.0.html).
Desktop applications additionally use the upstream [desktop-entry specification](https://specifications.freedesktop.org/desktop-entry/latest-single/)
for `Type=Application`, `Exec`, `TryExec`, `Terminal` and menu categories.

The initial catalog classifies existing Rust executables. Empty `apps` and
`services` lists retain distinct conversion destinations; they do not claim
that the existing desktop or service programs have already been ported.
T-1132 and T-1133 still track Windows artifact provisioning and renderer ordering.
Selecting a category does not prove static linking, cross-build success or
runtime readiness; each conversion must verify those properties separately.
The [Rust linkage reference](https://doc.rust-lang.org/reference/linkage.html)
distinguishes executable crate types from C-runtime linkage and recommends
inspecting the resulting binary. A musl target defaults to a static C runtime;
foreign dependencies and explicit compiler flags still require artifact checks.

`[build.native.linux]` now selects the musl target per architecture, the
`rust-lld` linker, static C-runtime flags and the release Cargo job budget.
The shared installer bootstraps the management executable for the compiler's
host, provisions the selected target standard library through an available
toolchain manager, then builds every catalog executable for that target.
A missing standard library or linker fails before release installation.
The host bootstrap executable is a build tool and is not copied into the image.

`miosd native-artifact-check <file> --arch <architecture>` validates ELF64
headers, target architecture, executable entry placement and segment bounds.
It rejects `PT_INTERP` and `DT_NEEDED`, while permitting static PIE relocation
tables. Installation uses this check for every selected release artifact;
executable permissions and ELF magic alone do not certify static linkage.
Both declared target architectures require their own build and runtime evidence.

The native verifier also covers Windows PE64 artifacts. `[build.native.windows]`
selects the target, linker, static runtime flags and exact `system_dlls` policy.
OS DLL imports are permitted; undeclared compiler or third-party runtime DLLs,
foreign architectures, truncated headers, invalid entry points and unsupported
delay imports fail. This is linkage evidence, not Authenticode or service health.
The PE format follows the [Microsoft specification](https://learn.microsoft.com/windows/win32/debug/pe-format).

Build preparation must execute `miosd native-toolchain-check --root . --lint`.
It checks the actual SSOT-required Clippy/rustfmt commands and runs warning-fatal
Clippy for host-applicable workspace packages. Windows cross-builds use
`miosd native-windows-build --root . --binary NAME --target-dir DIRECTORY`;
that command lints the selected target, builds with SSOT flags and validates PE
before staging. `build-mios.ps1` validates both the candidate and installed
wallpaper executable before service startup. Missing checks fail installation.
Clippy installation follows the [upstream component instructions](https://doc.rust-lang.org/stable/clippy/installation.html).

Runtime artifact checks use the shipped catalog without Cargo or a compiler:

```bash
miosd native-runtime-check --root / --platform linux --arch x86_64
miosd native-runtime-check --root / --platform linux --arch aarch64
miosd native-runtime-check --root SOURCE_OR_STAGED_ROOT --platform windows --arch x86_64 --bin-dir WINDOWS_BIN_DIRECTORY
```

Linux greenboot invokes this same verifier before reporting core health. A
Windows caller can run the verifier in MiOS-DEV against the mounted bin directory.
These checks require every selected artifact to exist and satisfy the applicable
SSOT linkage policy; they do not certify GPU, network, UKI or service readiness.
Runtime verification does not require build-only lint tools to remain installed.

Historical finding, recorded during the 2026-10-07 AGY continuity handoff:
“The actual fastfetch release binary also fails the static-linkage gate because
it requires glibc. Clippy is unavailable in the Linux builder, so lint verification
remains open.” Installing the declared toolchain components and rebuilding for
the SSOT musl target addresses the cause; every actual installed artifact and
platform still needs its own executed receipt. Never certify a debug binary
copied into a release directory or infer Windows/ARM64 readiness from x86_64 Linux.

`Containerfile` prefetches both locked workspaces with `--target host-tuple`.
[Cargo fetch](https://doc.rust-lang.org/cargo/commands/cargo-fetch.html) otherwise
fetches every platform, including the Windows GUI dependencies. The shared
installer provisions the SSOT musl target and builds its selected Linux catalog.
The current release focuses on x86_64; ARM64 runtime and image validation remain
roadmapped under T-1149.
Rustup component downloads use a separate build cache so an interrupted fetch
can resume on the next build. Rustup still verifies the component checksum;
the cache is not copied into the final image.

## Projection preparation

Stage the intended source changes before running `tools/sync-generated.sh`.
ROADMAP language metrics and the tracked-size ceiling measure Git index blobs,
so unstaged edits are intentionally excluded. Stage the resulting projections
and rerun synchronization until it makes no further changes before committing.

AI metadata and the manual corpus read Git index file modes. Compatibility
symlinks remain tracked, but their target content is indexed only under its
canonical source path. Linux links and Windows link placeholders therefore
produce the same content census.

## Build requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU cores | 4 | 8+ |
| RAM | 8 GB | 16+ GB |
| Disk (builder) | 100 GB | 250 GB |
| Network | Required (RPM + base image pulls) | Fast for ~2-4 GB of RPM downloads |

dnf5 cache mounts (`Containerfile` `--mount=type=cache,...`) make
subsequent rebuilds 5-10× faster.

## Future work: image-builder-cli

`bootc-image-builder` (BIB) is the current disk-image generator.
`image-builder-cli` (<https://github.com/osbuild/image-builder-cli>) is
under evaluation as a successor -- adds first-class SBOM generation and
cross-architecture support. `image-versions.yml` has commented-out
entries for `image_builder_cli_digest` ready for Renovate tracking.

## References

- bootc: <https://github.com/containers/bootc>
- bootc-image-builder: <https://github.com/osbuild/bootc-image-builder>
- rechunk: <https://github.com/hhd-dev/rechunk>
- cosign: <https://github.com/sigstore/cosign>
- Bootstrap repo: <https://github.com/mios-dev/mios-bootstrap>
