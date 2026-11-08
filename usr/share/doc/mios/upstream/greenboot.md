<!-- AI-hint: Documentation for greenboot, the automated post-boot health-check and auto-rollback system that protects MiOS's bootc image lifecycle — if required checks (composefs root integrity, mios-role, podman.socket, DNS) fail across repeated boots, the bootloader reverts to the last-good deployment, making "bootc upgrade" a safe Ctrl-Z-able operation.
     AI-related: mios-verify-root, verify-root.sh, mios-role.service, podman.socket, greenboot-healthcheck.service, greenboot-rollback, greenboot-status.service, redboot-auto-reboot.service, greenboot.conf, multi-user.target -->
# greenboot — Post-Boot Health Checks & Auto-Rollback

> Mentioned in `llms.txt` as part of MiOS's operational stack. Packaged via
> `usr/share/mios/mios.toml` `[packages.updater]` (`greenboot`,
> `greenboot-default-health-checks`); wired by `automation/46-greenboot.sh`.

## Purpose within MiOS

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped Fedora
workstation** (the whole OS is a single container image you `bootc upgrade` like a
`git pull` and `bootc rollback` like a Ctrl-Z) that is *also* a **local,
self-replicating, agentic AI operating system**. The atomic, image-based lifecycle
is what makes both halves safe to evolve — but an upgrade is only truly safe if a
bad image can be undone *without the operator present*.

greenboot is that automatic undo. It is the safety net that completes the bootc
story: `bootc upgrade` stages a new deployment, the system reboots into it, and
greenboot runs a battery of health checks. If the new deployment boots far enough
to run checks but fails them — or hangs — greenboot counts the failure and, after
a threshold of consecutive failed boots, lets the bootloader fall back to the
previous deployment automatically. Without greenboot, a broken `bootc upgrade`
could brick an unattended host; with it, the worst case is a few reboots followed
by an automatic return to the last-known-good image.

This matters doubly for MiOS because the image carries far more than a desktop:
the local agent stack (agent-pipe, MiOS-Hermes, the inference lanes, the
`mios-pgvector` datastore), GPU passthrough (NVIDIA/ROCm/iGPU via CDI), KVM/libvirt
virtualization, and an optional k3s+Ceph cluster path all ship in the same atomic
unit. greenboot validates the *foundation* those layers stand on — that the root
filesystem is intact, the role applied, the container engine up, and the network
reachable — so a regression in the base never strands the agentic plane.

## Upstream project

- Repo (Rust rewrite): <https://github.com/fedora-iot/greenboot-rs>
- Repo (original): <https://github.com/fedora-iot/greenboot>
- Fedora docs: <https://docs.fedoraproject.org/en-US/iot/greenboot/>

MiOS uses **greenboot-rs ≥ v0.2.0** — the Rust rewrite that is the default on
Fedora 43+ (the ucore/bootc base MiOS builds on).

## What it does

greenboot runs a set of health-check scripts shortly after each boot, ordered into
two tiers:

- **`required.d/`** — a failure here is *fatal*: it counts toward the rollback
  threshold. These guard the things that must be true for the deployment to be
  considered good.
- **`wanted.d/`** — a failure here is *advisory only*: it is logged to
  `greenboot-status` as a warning but never triggers a rollback. These cover
  role-specific or optional subsystems that may legitimately be absent.

A dedicated **`fail.d/`** handler runs before the rollback reboot to capture
diagnostics. The bootloader keeps a per-deployment boot-attempt counter
(`greenboot-grub2-set-counter.service`); on success it is cleared
(`greenboot-grub2-set-success.service`), and after
`GREENBOOT_MAX_BOOT_ATTEMPTS` consecutive failures the
`greenboot-rpm-ostree-grub2-check-fallback.service` flips the default to the
previous deployment (`redboot-auto-reboot.service` performs the reboot).

## Configuration (`usr/lib/greenboot/greenboot.conf`)

Per Architectural Law 1 (USR-OVER-ETC), MiOS ships its greenboot tuning as static
config under `/usr/lib/greenboot/`; `/etc/greenboot/` stays available for
admin-only overrides.

| Setting | Value | Meaning |
| --- | --- | --- |
| `GREENBOOT_MAX_BOOT_ATTEMPTS` | `3` | Consecutive failed boots before `bootc` rollback is triggered |
| `GREENBOOT_WATCHDOG_CHECK_ENABLED` | `true` | Integrate with a hardware watchdog timer (catches a *hung* boot, not just a failed check) |
| `GREENBOOT_WATCHDOG_GRACE_PERIOD` | `1` (hour) | Grace window after an upgrade before watchdog enforcement, so first-boot services (role-apply, freeipa-enroll, etc.) can settle |

## How MiOS uses it — the checks

The check scripts live in `usr/lib/greenboot/check/` (vendor-shipped; made
executable by `automation/46-greenboot.sh`).

### Required checks (fatal → count toward rollback)

| Check | Verifies | Mechanism |
| --- | --- | --- |
| `10-mios-composefs.sh` | Root filesystem integrity and composefs verity sealing | When ostree-booted, runs `/usr/libexec/mios/verify-root.sh` (`/usr/lib/os-release` parseable, `/usr` mounted read-only, best-effort fsverity/ostree-commit probes); then, if `enabled = verity` in `/usr/lib/ostree/prepare-root.conf`, confirms root is mounted `type composefs` and samples fsverity on a core binary (`/usr/bin/bash`). Both halves always run; either failing fails the check |
| `10-mios-role.sh` | The host role applied successfully | `mios-role.service` is active **and** `/var/lib/mios/role.active` exists |
| `20-podman.sh` | Container engine is up | `podman.socket` is active (the whole agent + service plane is Quadlet-driven, so a dead socket means a dead system) |
| `30-network.sh` | Network/DNS reachability | Waits up to 30s for `systemd-resolve ghcr.io` to succeed (DNS-only, no `curl`/`wget` dependency); a broken resolver after upgrade is rollback-worthy because Day-2 `bootc upgrade` itself needs the registry |

### Wanted checks (advisory → warning only, never rollback)

| Check | Verifies | Notes |
| --- | --- | --- |
| `30-nvidia-cdi.sh` | GPU passthrough wiring | Only when `/dev/nvidia*` is present: a CDI spec exists (`/var/run/cdi/` or `/etc/cdi/`) and `nvidia-ctk cdi list` reports `nvidia.com/gpu`. No GPU → skip |
| `40-role-target.sh` | Sane default boot target | `systemctl get-default` is a `mios-*.target`, `graphical.target`, or `multi-user.target` |
| `50-mios-ha-cluster.sh` | HA cluster health | Only if `pacemaker.service` is active and the cluster is bootstrapped; `pcs cluster status` must be clean. Skips on VM/WSL2 |
| `60-k3s.sh` | k3s readiness | Only if `k3s` is enabled for the role; waits up to 60s for active + readable kubeconfig + a `Ready` node. Desktop/hybrid roles (k3s disabled) skip entirely |

The split is deliberate: the *required* checks are role-agnostic invariants of any
MiOS deployment, while the *wanted* checks cover capabilities that legitimately
differ across the desktop / hybrid / cluster roles — so a desktop never rolls back
for "no k3s," and a cluster node still warns loudly if Pacemaker is sick.

### Failure logging

`fail.d/00-log-fail.sh` appends a timestamped block (plus `systemctl --failed`) to
`/var/log/greenboot.fail` and `sync`s it to disk **before** the rollback reboot, so
the failure reason survives into the previous deployment for post-mortem.

## bootc + greenboot interaction

greenboot is the second half of the bootc Day-2 story (see
`usr/share/doc/mios/upstream/bootc.md`). Order of events on
`bootc upgrade && systemctl reboot`:

```
1. bootc stages new deployment in /sysroot/ostree/deploy/<csum>.<n+1>
2. systemd reboots
3. bootloader picks the new deployment as default and increments its boot-attempt counter
4. mios-verify-root.service runs the early composefs/ostree integrity check (before basic.target)
5. greenboot-healthcheck.service runs after multi-user.target (and network-online.target)
6a. all required checks pass → greenboot marks the deployment "good" and clears the counter
6b. a required check fails → fail.d logs the reason; the boot counts as failed
7.  after GREENBOOT_MAX_BOOT_ATTEMPTS (3) consecutive failures → bootloader default flips to <csum>.<n>
8.  on the next boot the system runs the previous (last-good) deployment
```

`mios-verify-root.service` runs the root-integrity check *early* (before
`basic.target`, in a locked-down sandbox: `ProtectSystem=strict`, read-only
`/usr`+`/etc`, no network); a failure there also causes greenboot to roll the
deployment back. The user sees a notification in Cockpit if a rollback occurred.

## WSL2 caveat

On WSL2 greenboot is intentionally inert: `greenboot-success.target` and
`greenboot-healthcheck.service` carry a `ConditionVirtualization=!wsl` drop-in
(`greenboot-success.target.d/10-mios-wsl2.conf`). The cascade depends on
`boot-complete.target` → `boot.mount` → `/dev/disk/by-label/boot`, a block device
WSL2 lacks; and auto-rollback is moot under WSL2, which has no bootloader to count
against.

## Cross-refs

- `usr/share/doc/mios/upstream/bootc.md` — the upgrade/rollback mechanism greenboot guards
- `usr/share/doc/mios/80-security.md` — image-signing + trusted-boot posture
- `usr/libexec/mios/verify-root.sh` — early composefs/ostree integrity check
- `automation/46-greenboot.sh` — service-wiring (enablement + chmod) for the checks
