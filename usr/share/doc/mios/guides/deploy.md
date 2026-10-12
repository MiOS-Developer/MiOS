<!-- AI-hint: Documentation for deploying a built MiOS image -- the OCI artifact plus its RAW/ISO/QCOW2/VHDX/WSL2 disk forms -- onto bootc-managed or FHS Fedora hosts, and the Day-2 bootc lifecycle (upgrade/switch/rollback). Covers image signing/verification (cosign keyless), the layered mios.toml configuration surfaces, post-deploy health checks (incl. local AI sanity), and the self-hosted mios-forge Git origin that closes the self-replication loop.
     AI-related: /etc/mios/install.env, /etc/mios/mios.toml, /etc/mios/manifest.json, /etc/mios/ai/system-prompt.md, /usr/share/mios/ai/system.md, /etc/mios/forge/admin-password, mios-bootstrap, mios-dev, mios-ci, mios-forge, mios-forge-firstboot, mios-llm-light, mios-pgvector -->
# Deployment

## Purpose

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is a single container image -- boot it,
`bootc upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is
*also* a **local, self-replicating, agentic AI operating system** (a full
inference + agent stack behind one OpenAI-compatible endpoint). `self-build.md`
covers the first half of that lifecycle -- **build pipeline -> OCI image**. This
document covers the second half -- **OCI image -> running host**: how to place a
built `MiOS` image onto a machine and carry it forward atomically over its
lifetime.

Because the whole OS is a single image, deployment is not a sprawl of installers;
it is one of two acts -- *boot a disk artifact cut from the image*, or
*`bootc switch` an already-Fedora-bootc host onto the image ref* -- after which
every Day-2 change is `bootc upgrade` / `rollback` against that same ref. That
single-image discipline is what makes the AI plane trustworthy too: the agent
stack (inference lanes, agent-pipe/Hermes orchestration, the PostgreSQL+pgvector
memory) is baked into the same immutable image, version-locked to the OS, and
reproduced exactly on every host that pulls the ref.

For build instructions see
[`usr/share/doc/mios/guides/self-build.md`](self-build.md).

## Targets

`MiOS` produces one OCI image and several disk-image artifacts derived from it
via `bootc-image-builder` (BIB) -- see `Justfile`. The OCI image is canonical;
the disk artifacts are just that one image laid down in different boot/VM
formats so you can reach a running host from whatever you already have.

| Target | `just` recipe | BIB config | Output |
|---|---|---|---|
| OCI image | `just build` | -- | `localhost/mios:latest` |
| RAW (80 GiB ext4) | `just raw` | `config/artifacts/bib.toml` | `output/*.raw` |
| Anaconda ISO | `just iso` | `config/artifacts/iso.toml` + rendered account | `build/iso/` |
| QCOW2 | `just qcow2` | rendered from the SSOT | `build/qcow2/` |
| VHDX (Hyper-V) | `just vhdx` | rendered from the SSOT | `build/vhdx/disk.vhdx` |
| WSL2 tarball | `just wsl2` | `config/artifacts/wsl2.toml` | `output/*.wsl2` |

`iso`, `qcow2` and `vhdx` build through `miosd artifact-build <format>`, which
renders the BIB config per build and needs the operator's credential:
`MIOS_USER_PASSWORD_HASH` (`openssl passwd -6`) and/or `MIOS_SSH_PUBKEY`, or the
`[auth]` keys `[deploy.identity]` names. With neither it refuses; there is no
default password.

## Bootc-managed Fedora host (preferred)

This is the native path: the host's `/usr` becomes a read-only composefs mount
of the image, `/etc` gets a 3-way merge across upgrades, and `/var` survives
everything -- so the deployed system *is* the image, and upgrade/rollback are
image operations rather than package gambles.

End-user install (from `mios-bootstrap.git`):

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/install.sh)"
```

The bootstrap installer prompts for username, hostname, password (all defaulting
to `mios`), then runs:

```bash
sudo bootc switch ghcr.io/mios-dev/mios:latest
sudo systemctl reboot
```

Day-2 lifecycle -- the entire point of the bootc model: every change to the OS
(and to the AI stack baked into it) is one of these three image operations:

```bash
sudo bootc upgrade && sudo systemctl reboot   # Pull and stage next image
sudo bootc switch <ref>                       # Move to a different tag
sudo bootc rollback                           # Undo most recent upgrade
```

## FHS Fedora Server host (non-bootc)

The same one-liner. On a non-bootc host, bootstrap clones this repo and runs the
FHS overlay applier at `install.sh` to populate `/usr/lib/`, `/etc/`, etc.
directly. This trades away the atomic upgrade/rollback guarantees of the bootc
path for the ability to layer MiOS onto an existing Fedora Server install.

`install.sh` refuses to run on a bootc-managed host -- switch via `bootc switch`
instead.

## ISO install

Boot the Anaconda ISO produced by `just iso`, run the installer, reboot. On
first boot the deployed system runs `bootc upgrade` to align with the remote
tag, so a freshly-installed host converges onto the same ref every other MiOS
host tracks.

## VM install

Hyper-V: a Generation 2 VM on `disk.vhdx` with Secure Boot on the Microsoft UEFI
CA template, shaped by `[deploy.formats.vhdx.vm]`; the PowerShell is in
`usr/share/doc/mios/upstream/deploy-targets.md`. The VHDX is built locally from
the published digest, never downloaded (GitHub assets stop at 2 GiB a file).

QEMU/KVM: `qemu-system-x86_64 -enable-kvm -drive file=output/*.qcow2,if=virtio
-bios /usr/share/edk2/ovmf/OVMF_CODE.fd ...`.

WSL2: `wsl --import 'MiOS' C:\WSL\'MiOS' output/disk.wsl2`.

## Image verification

The image is the unit of trust, so verify it before you boot it. CI signs every
tag with cosign keyless (`.github/workflows/mios-ci.yml`):

```bash
cosign verify \
  --certificate-identity-regexp="https://github.com/mios-dev/mios" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" \
  ghcr.io/mios-dev/mios:latest
```

## Configuration surfaces

A deployed host is configured along the layered SSOT (`mios.toml`, highest
override wins) plus the bootc-managed config surfaces below. Vendor defaults
ship immutable in `/usr/share/`; `/etc/` is admin-override territory (Law 1,
USR-OVER-ETC).

| Surface | Path | Editable |
|---|---|---|
| Tunable SSOT (packages, ports, AI lanes, services) | `/etc/mios/mios.toml` overrides `/usr/share/mios/mios.toml` | Admin (run `mios-sync-env` after) |
| Identity (user, hostname) | `/etc/mios/install.env` | First-boot bootstrap, then admin |
| Service-level config | `/etc/mios/manifest.json` | Admin |
| Quadlet sidecars | `/etc/containers/systemd/mios-*.container` | Admin via drop-ins |
| Kernel kargs | `/usr/lib/bootc/kargs.d/*.toml` (vendor) or `bootc kargs edit` (admin) | Admin |
| Sysctl | `/etc/sysctl.d/` overrides `/usr/lib/sysctl.d/99-mios-*.conf` | Admin |
| AI prompt | `/etc/mios/ai/system-prompt.md` overrides `/usr/share/mios/ai/system.md` | Admin |

`/etc/mios/install.env` is the shell/systemd bridge derived from `mios.toml`;
regenerate it with `mios-sync-env` after editing the TOML. The browser-local
configurator UI at `/usr/share/mios/configurator/index.html` edits the same
`mios.toml`.

## Verification on a deployed host

Confirm both halves of the system after deploy -- the immutable OS posture *and*
the local AI plane. The `mios "..."` check exercises the whole AI throughline
(front door -> agent-pipe orchestration -> `mios-llm-light` inference ->
pgvector memory) in one shot:

```bash
bootc status                              # Image ref, deployment state
systemctl --failed                        # Any failed units
mios "what is the current image tag?"     # Local AI sanity (exercises the agent stack)
firewall-cmd --list-all                   # Firewall posture
getenforce                                # SELinux mode (must be Enforcing)
just forge                                # Self-hosted Git forge status + admin info
```

The AI plane resolves through the single OpenAI-compatible endpoint
`MIOS_AI_ENDPOINT` (Law 5). Behind it the
agent-pipe (port key `agent_pipe`) routes and fans out, MiOS-Hermes (port key
`hermes`) runs the tool-loop, **inference and embeddings** run on
`mios-llm-light` (port key `llm_light`, the primary llama.cpp lane; the gated
heavy GPU lanes `mios-llm-heavy`/`-alt` stay inert until enabled), and
**PostgreSQL+pgvector** (`mios-pgvector`, `:5432`) holds the unified agent
memory. If `mios "..."` answers, that chain is live.

### Self-hosted Git forge (`mios-forge`)

The forge is not an afterthought -- it is the **origin of the self-replication
loop**. MiOS pushes its own source to this forge, CI builds the next image from
it, and `bootc switch`/`upgrade` deploys that image: the box can rebuild and
re-deploy itself with no external Git host in the loop. That is why
`mios-forge` is enabled by default and must run on every shape, including WSL.

The `mios-forge.container` Quadlet (Forgejo upstream) starts at first boot. The
`mios-forge-firstboot.service` oneshot runs after it, reads the admin identity
from `/etc/mios/install.env` (which `build-mios.{sh,ps1}` populated from
`mios.toml`), generates a random initial password if none was supplied, and
creates the admin user via the in-container `forgejo admin user create
--must-change-password=true` CLI. The sentinel at
`/var/lib/mios/forge/.firstboot-done` makes the service idempotent across
reboots.

```bash
just forge                                       # status + URL + admin info
sudo cat /etc/mios/forge/admin-password          # one-time password read
git remote add origin http://localhost:3000/<user>/<repo>.git
git push origin main
```

The forge serves its web UI over HTTP `:3000` and git+ssh on `:49922` (Forgejo's
built-in SSH server; port `2222` was vacated for the host admin `sshd`, and
`22`/`2222` stay reserved for the host's own SSH stacks). Repository bytes live
at `/srv/mios/forge/git/`; the SQLite DB at `/srv/mios/forge/forgejo.db`. The
container runs as uid `816` (`mios-forge`) after a documented root-bootstrap
window required by the upstream s6-overlay image (the Law 6 exception is recorded
in the unit header alongside `mios-ceph`, `mios-k3s`, and `mios-forgejo-runner`).

## References

- `Justfile` (`just --list` for the full target set)
- [`usr/share/doc/mios/guides/self-build.md`](self-build.md) -- the build half of the lifecycle
- bootc: <https://bootc-dev.github.io/bootc/>
- bootc-image-builder: <https://github.com/osbuild/bootc-image-builder>
- Bootstrap repo (user-facing install): <https://github.com/mios-dev/mios-bootstrap>
