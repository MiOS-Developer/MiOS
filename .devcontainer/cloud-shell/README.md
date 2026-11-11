<!-- AI-hint: How to install and launch the portable MiOS Dev Container from Google Cloud Shell and Oracle Cloud Shell, which do not apply devcontainer.json on their own. -->
# MiOS Cloud Shell bootstrap

GitHub Codespaces automatically consumes `.devcontainer/devcontainer.json` and
runs its lifecycle commands. Google Cloud Shell and Oracle Cloud Shell do not
automatically apply a repository Dev Container configuration when a repository
is opened, so install the portable launcher once from the MiOS checkout:

```bash
bash .devcontainer/cloud-shell/bootstrap.sh install
```

Open a new shell, then run:

```bash
mios-cloud-shell up
```

The launcher installs `@devcontainers/cli` beneath persistent
`$HOME/.local`. On Oracle Cloud Shell it starts the rootless Podman API socket
and exposes it through `DOCKER_HOST` for the Dev Containers CLI. On Google
Cloud Shell it uses the preinstalled Docker engine. Both routes select the
portable `.devcontainer/devcontainer.json` profile.

Use the privileged `.devcontainer/artifact-builder/devcontainer.json` only on
a trusted, self-managed builder host. Cloud Shell providers do not promise the
privileges needed for `bootc-image-builder` disk artifacts.

## Claude Code cloud environment

A Claude Code cloud session runs on a fixed Ubuntu VM, so MiOS reaches it as
the same dev image Codespaces builds: `claude-code-cloud.sh` builds
`.devcontainer/Containerfile` (FROM the machine-os mirror every MiOS image
shares) from MiOS `main` under rootful podman, runs the devcontainer
lifecycle, and installs `mios-dev`, which enters that image with the session's
checkouts mounted at the same paths. The generic projection and the dev-loop
plugin come from the dev-loop toolkit; this file supplies only MiOS's values.

In the environment's settings (environment menu, Edit):

Setup script:

```bash
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/mios-dev/MiOS/main/.devcontainer/cloud-shell/claude-code-cloud.sh -o /tmp/mios-claude-code-cloud.sh && bash /tmp/mios-claude-code-cloud.sh
exit 0
```

Environment variables:

```
CLAUDE_CODE_PLUGIN_DIRS=/opt/dev-loop
```

Every `FEDORA_*` value has a default in the script; set one in the environment
variables only to override it. A cold first build can exceed the setup budget:
past `FEDORA_SETUP_BUDGET_S` the lifecycle is deferred, `mios-dev` says so, and
`bash /opt/dev-loop-fedora/cloud-fedora-setup.sh --lifecycle` applies it.

### Tailnet enrollment

The cloud overlay (`/etc/mios/mios.d/50-cloud.toml`, rendered from
`mios.toml [deployment.cloud.overlay]`) carries the node's launch parameters
in `[deployment.cloud.overlay.tailscale]`: userspace networking (no TUN, an
HTTPS-only egress proxy, so the tailnet is reached over DERP), a loopback
SOCKS5 listener at `127.0.0.1:1055`, hostname `mios-cloud` (the tailnet
suffixes duplicates), an exit-node offer, shields up and no DNS takeover. The
devcontainer start (`boot-mios-systems.sh start`) launches `tailscaled` with
them on a private socket and `mios-tailscale-sync` enrolls a logged-out node
with `tailscale up --auth-key=file:...`.

The pre-auth key is never in this repository, a command line or a log. Give
the host one of: `TS_AUTHKEY_FILE` (a 0600 file), or `MIOS_TAILSCALE_AUTHKEY` /
`TS_AUTHKEY` in the session environment. The lifecycle passes it into the
container by name only (`podman exec -e NAME`), and the sync writes it to a
temporary 0600 file for the enrollment and deletes it. Use a short-lived,
pre-authorized key; an exit-node offer still needs approval in the tailnet
admin console unless an `autoApprovers` rule covers the key's tag. Without a
key the node stays logged out and `tailscale up` must be run by hand.

SSH to a tailnet peer through the listener:
`ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:1055 %h %p' <user>@<tailnet-ip>`.

## Codex Cloud environment

The main [README's Codex Cloud section](../../README.md#codex-cloud-environment)
contains paste blocks for every environment-editor field. `codex-cloud.sh`
installs the Dev Containers CLI, starts the canonical MiOS Fedora devcontainer
through Podman, applies its create/start lifecycle and installs `mios-dev`.
The wrapper runs commands as the devcontainer user; it maps the primary checkout
to `/workspaces/MiOS` and selected public sibling checkouts to their SSOT paths.

Installation requires a cloud control host that permits Podman containers. It
fails when the runtime, build, lifecycle, Fedora check, required CLI execution
or gateway readiness check fails. It does not replace the provider's host OS.
The shared Containerfile owns package and agent installation from SSOT; this
script installs only control-host prerequisites and the container launcher.

Run `/usr/local/libexec/mios-codex-cloud start` at task startup; run its `check`
mode to repeat readiness checks. A saved cloud filesystem does not imply that
background services or containers survived a restart. No credentials are added
to images or automatically read from the private `.secrets` repository.
