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

## In-place MiOS-DEV (no image pull)

A reusable skill for agents: copy this section as `SKILL.md` (front matter `name: mios-dev-inplace`) into
your harness's skills directory to convert a cloud container or VM that cannot run the MiOS image.


Use this when a machine must meet the MiOS-DEV spec but cannot run the MiOS image:
too little disk for `[deployment.cloud].min_free_gb`, no nested containers, or a
fixed provider base image. The supported path is still the image
(`.devcontainer/Containerfile`, `cloud-shell/bootstrap.sh`); this skill is the
fallback, and every step maps back to the lifecycle it replaces
(`.devcontainer/boot-mios-systems.sh`, `setup-devcontainer.sh`, the Containerfile's
account wiring).

Validated end to end on Ubuntu 24.04 (Claude Code cloud: 4 CPUs, 15 GB RAM, ~26 GB
free, PID 1 not systemd, HTTPS-only egress proxy). Lines marked *(other hosts)* are
mapped from the same lifecycle but were not run there.

### Rules that do not bend

- **Never run `install-root-overlay.sh` or `boot-mios-systems.sh start` wholesale** on
  a non-MiOS host. They link `/.git` into `/` (every git command outside a repo
  then acts on MiOS with worktree `/`), copy Fedora PAM/sudoers/containers config and
  `profile.d` scripts into the host, and abort at the `bootc` gate anyway. Run the
  pieces below instead.
- **Protect the live link.** If the machine already carries a tailnet node and an
  MCP session, never restart or replace that `tailscaled`; drive it, don't own it.
- **Never run MiOS services as root.** `mios-mcp-server --project-agent-clients`
  rewrites `$HOME/.claude.json` (the running agent's own config). Everything runs
  as the canonical `[identity].username`.
- **Secrets stay in files.** A tailnet pre-auth key reaches `tailscale up` only as
  `--auth-key=file:<0600 path>` (what `mios-tailscale-sync` does); never argv,
  never env files, never logs.
- **Fail closed.** If a download has no verifiable digest or checksum, do not
  install it; MiOS's own fetchers refuse it, and so should you.

### Step 0: baseline and an invariant probe (both sides)

Before any change, write a probe that must stay green after every step: one
`tailscaled`, the SOCKS5 listener still served, the node still `Running` at its
address, no `/.git`, repos clean, the root login shell unchanged (CA/proxy
variables, no aliases), `visudo -c`, the agent proxy reachable, the agent's config
unmodified. Prove it can fail (point it at a bogus socket and see it go red).
Back up `/etc/{passwd,group,shadow,gshadow,subuid,subgid}` to a 0700 dir.

### Step 1: canonical account

- Read the spec: `usr/libexec/mios/mios-toml-get identity username` (and `shell`,
  `groups`). It must own **uid 1000**, home `/var/home/<user>`.
- If a stock account holds uid 1000 (`ubuntu`, `ec2-user`, `debian`, `codespace`)
  and nothing runs as it: `usermod --login <user> --home /var/home/<user>
  --move-home <old>`, `groupmod --new-name <user> <old>`, then rename its
  `/etc/subuid` and `/etc/subgid` lines (do not append a second range:
  `mios-subuid-alloc --check` flags the collision). If uid 1000 runs something,
  stop and ask.
- Groups from `[identity].groups`. Create missing names; never force MiOS's fixed
  GIDs onto ones the host already uses.
- `sudo`: one validated drop-in (`visudo -cf` before the `mv`). Do not copy the
  repo's `etc/sudoers.d`.

### Step 2: workspaces and home

- `/workspaces/<repo>` symlinks to wherever the checkouts already are
  (`[workspace].root`). Keep the checkouts owned by whoever owns them now;
  give the canonical user `safe.directory '*'`.
- Seed `/var/home/<user>` from `etc/skel` and the `install-root-overlay.sh` home
  block; write the devcontainer env contract (`MIOS_ROOT`, `MIOS_AI_ENDPOINT`,
  `PATH=/opt/mios/bin:…`) to `~/.config/mios/devcontainer-runtime.env` sourced from
  `~/.profile`.
- Point every CA variable at the **system** bundle (`/etc/ssl/certs/ca-certificates.crt`
  on Debian/Ubuntu, `/etc/pki/tls/certs/ca-bundle.crt` on RHEL). A provider bundle
  under `/root` is unreadable to the canonical user.

### Step 3: selective FHS overlay

Allowlist `usr`, `etc`, `var` only. `usr` entries become symlinks into the
checkout (read-only vendor tier, like bootc's `/usr`); `etc`/`var` entries are
copied **no-clobber** (never overwrite a host file); record every path in a
manifest. Exclude:

`etc/profile.d`, `etc/sudoers.d`, `usr/lib/sudoers.d`, `etc/pam.d`,
`usr/lib/pam.d`, `etc/containers`, `usr/lib/containers`,
`usr/share/containers/{storage.conf,containers.conf.d}`, `usr/lib/tmpfiles.d`,
`usr/lib/sysusers.d`, `etc/sysusers.d`, `usr/lib/sysctl.d`, `etc/sysctl.d`,
`etc/tmux.conf`, `etc/systemd/resolved.conf.d`, `usr/share/git-core`, `etc/skel`,
every top-level repo file and every non-FHS top-level directory.

(tmpfiles would replace a real `/nix`; sysusers would create a second uid-1000
account; sysctl would reserve 1 GiB; resolved.conf.d would take DNS from the
proxy.)

### Step 4: SSOT host drop-ins

- `bash .devcontainer/cloud-shell/bootstrap.sh overlay` to review, then
  `overlay --apply`: writes `/etc/mios/mios.d/50-cloud.toml` from
  `[deployment.cloud.overlay]` (userspace Tailscale, SOCKS5 `127.0.0.1:1055`, exit
  offer, shields up, no DNS takeover; no model pulls; headless).
- If a node is already enrolled, add a host pin
  (`/etc/mios/mios.d/60-tailscale-node.toml`: its `hostname`, `accept_routes =
  false`) so the first sync does not rename it. Check with
  `mios-tailscale-sync --socket=<sock> --dry-run`.

### Step 5: packages

Map `[packages.devcontainer]` (and the sections it requires) to the host's
package manager; install what has a candidate, record the rest. Two traps:

- **Order:** step 3 may already have placed an `/etc` file a package also ships
  (e.g. `/etc/zsh/zshrc`). The conffile prompt then hits EOF under a
  non-interactive install. Finish with `dpkg --configure -a --force-confold`
  (keeps the MiOS copy) *(rpm hosts: `.rpmnew` appears instead; keep the MiOS file)*.
- `bootc`, `bootupd`, `rpm-ostree`, `dnf5` have no Debian/Ubuntu candidate. Record it
  as unmet; never stub `bootc` to pass a gate.

### Step 6: native toolchain, binaries, venvs

- Rust: reuse the host toolchain (copy to `[build.toolchain]` homes); add
  `x86_64-unknown-linux-musl`. Build both workspaces `--locked --offline`
  (`src/mios-rs`, `tools/native`) and install into `/opt/mios/bin` plus the
  `[build.native.categories]` layout. Do not use `miosd native-build` here (it runs
  `rustup` updates).
- MCP venv `/usr/lib/mios/mcp/.venv` **offline** from
  `usr/share/mios/vendored/wheels` (`mcp uvicorn openai`); tmux-mcp from the SSOT
  asset with its `sha256` checked.
- Agent venv `/usr/lib/mios/agents/.venv` with `-c usr/share/mios/agents/constraints.txt`.

### Step 7: tools the generated config needs

- **tmux >= 3.5.** MiOS's generated `tmux.conf` sets `extended-keys-format`; tmux 3.4
  (Ubuntu noble) rejects it and every `mios_tmux_*` tool fails with "Connection
  closed". Build the upstream release into `/usr/local` and verify the tarball
  against an independent digest (Fedora dist-git `sources` SHA-512). Do not copy a
  Blade-built tmux: it is dynamically linked against a newer glibc.
- **code-server:** take `[image.sidecars].code_server` (an OCI ref): pull, record the
  digest, copy `/usr/lib/code-server` out, then
  `mios-vscode-custom-css patch` and `verify` the workbench. Running `--version` as
  root writes a default config with a password under `/root`; remove it.
- **oh-my-posh:** download from the release and require a match in upstream's
  `checksums.txt`.

### Step 8: Tailscale under SSOT

- Userspace mode when there is no TUN or an HTTPS-only proxy (traffic goes over
  DERP; expect ~3 KB/s on bulk transfers).
- Give the canonical user the live daemon without restarting it: bind-mount its
  socket at `/run/tailscale/tailscaled.sock` and `tailscale set --operator=<user>`.
- Run `mios-tailscale-sync --socket=/run/tailscale/tailscaled.sock` **as the
  canonical user**; compare `tailscale debug prefs` before/after (expect unchanged
  on an already-matching node). `usr/libexec/mios/test_mios_tailscale_sync.py` must pass.
- Fresh node: export `MIOS_TAILSCALE_AUTHKEY` (or set `[tailscale].auth_key_file`)
  and let the sync enroll; it writes the key to a 0600 temp file and removes it.

### Step 9: services and resume

As the canonical user: `mios-agent-pipe-dev start` (127.0.0.1 `[ports].agent_pipe`),
`mios-mcp-server --http --host 127.0.0.1 --port <[ports].mcp>`, code-server on
`127.0.0.1:<[ports].code_server>`. Write `/etc/mios/dev-cloud/resume.sh` that relaunches
all of them after a reclaim, because nothing supervises them when PID 1 is not
systemd *(GCE/AWS/Codespaces with systemd: user units or user Quadlets instead)*.

### Step 10: verification ladder

- Probe green after every step (step 0), and red against a planted fault.
- Local MiOS-MCP over stdio: `initialize`, `tools/list` (expect the SSOT floor plus
  `mios_tmux_*`), one `mios_tmux_execute_command` as the canonical user.
- `mios-agent-pipe-dev check`, `curl 127.0.0.1:<mcp>/health`, code-server `/healthz`.
- Report unmet spec items plainly (no `bootc`, no local model lane).

### Platform notes

| Platform | Differences |
|---|---|
| Claude Code / Codex / ChatGPT cloud containers | No systemd; egress proxy; disk reclaimed when idle. Use `resume.sh` from a session-start hook. No TUN: userspace Tailscale. |
| GCE / AWS VMs | systemd PID 1: install services as user units or Quadlets; kernel-mode Tailscale is fine; uid 1000 is often `ubuntu`/`ec2-user`/`admin`. |
| Codespaces / devcontainer hosts | Prefer the real image (`.devcontainer/devcontainer.json`); use this skill only when the image cannot be built. |
| Fedora/RHEL without bootc | `dnf` installs `[packages.devcontainer]` directly; `rpm` conffiles become `.rpmnew`; tmux and code-server usually meet the spec natively. |

### Remote MiOS-MCP traps (learned the hard way)

- `mios_tmux_execute_command` caps at `[mcp.tmux].timeout_s` (300 s) and its command
  goes through `tmux send-keys`, which rejects very long commands: upload scripts over
  SSH, start them with `setsid nohup`, and poll a `.done` file.
- The MCP helper shell has a private `HOME`: git refuses root-owned worktrees as
  "dubious ownership" unless the script sets `GIT_CONFIG_COUNT=1
  GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'`.
- `ssh` inside `while read` loops needs `-n`, or it swallows the loop's input.
- A Blade checkout may be a partial clone (`blob:none`) with lazy fetch off. Fetch
  from it with `--filter=blob:none` and a per-process `--upload-pack="git -c
  uploadpack.allowFilter=true -c uploadpack.allowAnySHA1InWant=true upload-pack"`; recover
  missing blobs only from checked-out files that hash to the exact object id.

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
