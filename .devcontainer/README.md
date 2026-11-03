<!-- AI-hint: Portability contract for the MiOS devcontainer harness: one Containerfile image, its lifecycle scripts, and how Codespaces and cloud shells bring the MiOS, mios-bootstrap and -dev-loop workspace up. -->
# MiOS devcontainer harness — portability contract

The committed `devcontainer.json` is the portable MiOS development baseline.
It provisions the MiOS, mios-bootstrap, and -dev-loop workspace layout and
automatically initializes the development core on every supported Dev
Container connection, including GitHub Codespaces.

## One image, every environment

`Containerfile` layers only account and container wiring on `[image].ref`.
Packages, toolchains and application payloads belong to the OS pipeline.
mios-bootstrap and -dev-loop carry byte-identical copies of this Containerfile;
edit this source and synchronize its mirrors.

The checkout's `[identity].username` projects both `remoteUser` and
`build.args.MIOS_IDENTITY_USERNAME`. The build materializes sysusers before
requiring the account, preserves UID 1000, initializes its home and workspace,
and validates noninteractive development sudo policy. A known image identity can be
renamed to the selected checkout identity while keeping its UID and group
membership. Subordinate UID/GID mappings follow that account for rootless
container builds. An unrelated account occupying UID 1000 fails the build.
The development session runs as that account, never as root.

The wrapper requires the base image to provide executable
`/usr/libexec/mios/mios-toml-get` and
`/usr/libexec/mios/mios-subuid-alloc` before any account changes. Missing
helpers require a current full MiOS image from the OS pipeline. Rebuilding
the thin wrapper cannot add the missing OS payload. During a registry
migration, `[image].name` identifies the publication target while
`[image].ref` remains the released consumer reference until the new image
is published and verified; source account tests do not prove that a retained
registry image provides these helpers.

Both image publishers run the wrapper's exact account instruction in the
newly built MiOS image, then attach as the configured user and check UID 1000,
the home, workspace access and noninteractive sudo. The gate also compares
`/usr/share/mios/source-revision`, written from the build context's Git
commit, with the publishing checkout. An inherited OCI revision label is not
the source proof. A failed smoke blocks publication; it does not start
sidecars or update a running Codespace.

## Recovering a missing-account startup

An error such as `unable to find user user: no matching entries in passwd file`
occurs before post-create commands can run. Creating an account in
`postCreateCommand` cannot repair that attach path. The recovery container is a
separate fallback environment; its account list does not describe the failed
MiOS image.

Keep source changes and required files under `/workspaces`, update the checkout
to the repaired configuration, then perform a full container rebuild. A
reconnect or start of an existing container keeps its old passwd database and
image metadata. GitHub documents that a rebuild preserves the workspace; state
outside that workspace needs to be saved separately.
[Codespaces rebuild documentation](https://docs.github.com/en/codespaces/developing-in-a-codespace/rebuilding-the-container-in-a-codespace)

From an authenticated operator terminal, a full rebuild can also be requested
for the selected codespace:

```sh
gh codespace rebuild --codespace <codespace-name> --full
```

The cloud-shell launcher checks the selected image and account before running
lifecycle commands. It refuses an existing container made from an older image
and leaves that container intact. Preserve its non-workspace state before
recreating it from the rebuilt image; the source checkout remains in its bind
mount.

## Development-safe MiOS core

The image includes rootless Podman/Quadlet networking dependencies and the
agent-pipe runtime at `/usr/lib/mios/agents/.venv`, matching the MiOS service
contract. Post-create applies the complete MiOS root overlay, initializes the
developer's MiOS state directories, and builds both upstream native Rust
workspaces. Their release binaries are available from `/opt/mios/bin`, while
the in-tree release targets remain available to the normal `just` preflight
checks.

Every Dev Container start runs `boot-mios-systems.sh start`, which reapplies the
root overlay, incrementally rebuilds the source-matched native components, and
records the active platform contract before the verification gate runs. This
keeps resumed workspaces aligned with the checked-out MiOS system source rather
than relying on one-time creation state. The same boot step re-projects the
`.dotfiles` SSOT into the live editor `Machine/settings.json` so theme and
layout settings never drift, but the edge-to-edge terminal CSS itself
(`vscode_custom_css.imports`) is applied by the `be5invis.vscode-custom-css`
extension, which can only patch a real local workbench install. It is pinned
to run on the connecting client via `remote.extensionKind: {"be5invis.vscode-custom-css": ["ui"]}`;
after every client-side VS Code update, its own upstream docs require
re-running "Enable Custom CSS and JS" once and reloading the window — see
`docs/research/spike-vscode-edge-to-edge-terminal.md` for the full root-cause
trace. This is not automatable from inside the container.

Run `miosd --help` to inspect the native control-plane interface. Run
`mios-agent-pipe-dev` explicitly when developing the API gateway; it uses the
service-compatible runtime and checked-out source. Podman
sidecars, pgvector, inference lanes, systemd units, hardware services, and
host-security services are deliberately not started automatically. They need
host-specific privileges, devices, credentials, or persistent state and remain
explicit developer actions.

## Mobile portrait workbench

The Dev Container and its two workspace files default to Dark Modern with a
top Activity Bar, one primary side bar, a full-width bottom panel, horizontal
editor splits, word wrap, and larger editor and terminal text. This preserves
usable vertical space and touch targets in portrait remote sessions. VS Code
does not detect or rotate its layout for device orientation; users can still
override these defaults from the layout controls or their user settings.

## Platform-aware connection bootstrap

`.devcontainer/boot-mios-systems.sh create` records the detected platform and
portable profile in `~/.config/mios/devcontainer.env` during post-create.
The same lifecycle entrypoint runs ordered startup checks with `start`;
`platform` remains available as a focused setup mode.
GitHub Codespaces runs the Dev Container lifecycle automatically. Google Cloud
Shell can run a persistent `$HOME/.customize_environment` bootstrap at VM
start, while Oracle Cloud Shell retains standard shell initialization in its
persistent home; neither cloud shell automatically applies a repository's
Dev Container configuration by itself.

`.devcontainer/cloud-shell/bootstrap.sh install` installs the portable
Dev Containers launcher once under persistent `$HOME/.local`; use
`mios-cloud-shell up` from a later shell connection to create or update the
portable development environment. See
[Cloud Shell bootstrap](cloud-shell/README.md) for the provider-specific
details.

## Privileged artifact-builder profile

Use `.devcontainer/artifact-builder/devcontainer.json` on a trusted,
self-managed Podman/Docker host to run `just raw`, `just iso`, `just qcow2`,
or `just vhdx`. The profile enables privileged nested Podman and persistent
container storage for `bootc-image-builder`. The portable default supports
development, validation, and OCI workflows but does not claim that restricted
cloud platforms can locally produce privileged disk artifacts.

The privileged profile does not hardcode GPU, KVM, or host-specific device
mappings. Hardware-dependent checks remain conditional, and artifact builds
that need a particular device must request it explicitly.

## Adding local hardware passthrough (not committed)

If you are running this devcontainer on a MiOS-Metal Blade or another host
with KVM/NVIDIA passthrough available, add the extra `runArgs` in your local,
uncommitted Dev Containers configuration (for example your editor's
per-user `devcontainer.json` override, or a Podman CLI wrapper you keep outside
the repository). Do not add unconditional hardware-specific device mappings to
the committed configuration.
