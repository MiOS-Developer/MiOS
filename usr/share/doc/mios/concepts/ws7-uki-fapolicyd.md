<!-- AI-hint: Documentation of the WS-7 security architecture defining the transition from permissive fapolicyd observation to enforced execution whitelisting and verity-rooted UKI builds via mios.toml configuration flags. Explains how execution-integrity hardening serves MiOS's immutable bootc/agentic dual nature.
     AI-related: /etc/mios/mios.toml, mios-ws7-permissive, mios-agent-codegen, mios-ws7-uki, mios-deny, mios-verity, fapolicyd.service -->
# WS-7 — Verity-rooted UKI + fapolicyd execution whitelist

> Status: scaffolded 2026-06-04, **DEFAULT-OFF / OBSERVE-ONLY**. Companion:
> `aios-implementation-plan.md` (Appendix B · WS-7), `upstream/bootc.md`,
> `upstream/composefs.md`, `coderun-sandbox.md`.

## Where this fits in MiOS

MiOS is one thing built two ways at once: an **immutable bootc/OCI-shaped Fedora
workstation** (the whole OS is a single container image — boot it, `bootc
upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is *also*
a **local, self-replicating, agentic AI operating system** (a full agent stack
behind one OpenAI-compatible endpoint: agent-pipe orchestration, the MiOS-Hermes
gateway, pgvector memory, and the `mios-llm-light` inference lane on the
`llm_light` port).

Those two halves create a tension that WS-7 exists to resolve. The immutable
half wants a root of trust that goes all the way down — a kernel image and a
filesystem whose integrity can be *measured and required*, so a tampered image
refuses to boot. The agentic half deliberately does the one thing such a root of
trust forbids: it **writes and runs model-generated code**. WS-7 is the security
workstream that lets both be true at once — execution-integrity hardening for the
host, with a single, tightly-scoped carve-out for the place the agent is
*supposed* to execute untrusted code (the coderun sandbox).

Concretely, WS-7 ships two capabilities, both **default-off**:

- **fapolicyd execution whitelisting** — deny-by-default execution on the host,
  trusting only RPM-db-backed / fs-verity-trusted paths, plus an explicit
  carve-out for the sandboxed agent codegen path.
- **A verity-rooted Unified Kernel Image (UKI)** — a `ukify`-built kernel image
  measuring the composefs fs-verity digest, so the boot path can *require* an
  intact, tamper-evident root (`rootflags=verity.require`).

This document is the operator's reference for what the scaffold contains, why it
is intentionally inert, and the rollback-tested procedure for promoting it to
enforce on a real image. Audience: the operator/maintainer building and deploying
MiOS images.

## TL;DR — the one rule

**fapolicyd ships in PERMISSIVE (observe) mode and the verity UKI is a build
ARTIFACT that is never made the active boot entry by default.** Enforce-mode
fapolicyd on an incomplete whitelist, or a mis-signed / `verity.require` UKI,
**bricks boot** — and on an immutable composefs root there is no easy in-place
recovery once the deny has cut the recovery shell. Promotion to enforce is a
deliberate, rollback-tested operator step. Nothing in this scaffold flips it.

This is the single place in MiOS where the usual "default on, debug live" order
is *wrong*: a bad flip here doesn't degrade a service, it removes the box from
the network and from your hands. The bootc image lifecycle (build → deploy →
`bootc rollback`) is the safety net the procedure below leans on.

## What this scaffold ships

| File | Role | Default posture |
|---|---|---|
| `usr/lib/fapolicyd/mios-ws7-permissive.conf` | fapolicyd config drop-in, `permissive = 1` | observe; not applied unless gated on |
| `usr/lib/fapolicyd/rules.d/80-mios-agent-codegen.rules` | exec carve-out for the sandboxed agent codegen | inert until enforce |
| `usr/lib/fapolicyd/rules.d/90-mios-deny.rules` | deny-by-default block (evaluated last) | inert until enforce |
| `usr/lib/bootc/kargs.d/32-mios-ws7-uki.toml` | boot posture kargs | only `fapolicyd.permissive=1` active; enforce/lockdown/verity.require **commented** |
| `automation/lib/ws7-uki-fapolicyd-build.sh` | gated build step (observe install + verity UKI build) | no-op unless SSOT flags true; **not** a numbered pipeline step, so `build.sh` never auto-runs it |
| `usr/share/mios/mios.toml` (shared_edits) | SSOT flags `[security.fapolicyd_observe].enable`, `[uki].verity_uki_build` | both `false` |

### SSOT knobs (mios.toml, both default `false`)

Like everything operator-tunable in MiOS, these flow from the single
`mios.toml` source of truth (three-layer override: `~/.config/mios/` <
`/etc/mios/` < `/usr/share/mios/`).

```toml
[security.fapolicyd_observe]
enable = false   # install the permissive/observe drop-in + codegen carve-out

[uki]
verity_uki_build = false   # build the verity-rooted UKI artifact (ukify)
```

These flow `mios.toml -> tools/lib/userenv.sh (MIOS_FAPOLICYD_OBSERVE_ENABLE /
MIOS_UKI_VERITY_UKI_BUILD) -> the build step`. The build step ALSO reads them
directly from the resolved TOML via its `_ws7_scalar` helper, so it is correct
whether or not `userenv.sh` ran.

### The codegen carve-out — where the two halves of MiOS meet

This file is the load-bearing seam between the immutable host and the agentic
brain. fapolicyd enforce mode would deny the agent's *legitimate* model-generated
code from executing — even though that code only ever runs inside the coderun
sandbox (rootless podman, `Network=none`, `DropCapability=ALL`, seccomp, writable
`/work`+`/tmp` only; see `coderun-sandbox.md`). The agent stack
(agent-pipe → Hermes → the `coderun`/`code_mode` verbs) is *designed* to write
and run untrusted code there; without an explicit allow, enforce-mode fapolicyd
would break the one place that is supposed to happen.

`80-mios-agent-codegen.rules` re-permits exec **scoped to the sandbox roots only**
(resolved from `mios.toml [paths].coderun_workspace_root` /
`.coderun_snapshots_root` / `ai_scratch_dir` — by default
`/var/home/mios/coderuns`, `/var/home/mios/.coderun-snapshots`, and
`/var/lib/mios/ai/scratch` — rendered by the build step). It is numbered `80-` so
it is evaluated **before** the `90-mios-deny.rules` deny-by-default block (rules.d
is read lexicographically, first match wins). It is inert while permissive, and
it never broadens execution on the operator's real home or system paths.

## Promotion procedure: permissive → enforce (operator-gated)

Do this **only** in a dedicated image-build + boot-test session, never as part
of the "everything on" flip. Have a known-good prior bootc deployment staged
so `bootc rollback` is a one-command recovery. (This is exactly why MiOS is
shaped as an immutable bootc image: the prior deployment stays staged, so the
worst case is one command back to a booting box.)

1. **Enable observe.** Set `[security.fapolicyd_observe].enable = true` in
   `/etc/mios/mios.toml`, run the gated build step (or wire it on per the
   shared_edit below), rebuild + deploy. fapolicyd now LOGS would-be denials
   and blocks nothing.

2. **Collect the would-deny log.** Run the box through a full week of real
   workloads (every service start, every agent turn, every coderun dry-run).
   Read what fapolicyd *would* have denied:
   ```bash
   journalctl -u fapolicyd | grep -E 'dec=deny'
   # or the richer report:
   fapolicyd-cli --dump-cache | less
   ```

3. **Close the gaps in the whitelist, NOT by re-broadening.** For each
   legitimate path fapolicyd flagged, add a targeted
   `allow perm=execute path=/exact/path : all` to a new `rules.d/` file (do
   **not** widen the `uid=0` catch-all in `etc/fapolicyd/fapolicyd.rules`).
   Re-build, re-deploy, repeat step 2 until the would-deny log is empty across
   a full workload cycle (including a fresh boot, a `bootc upgrade`, an agent
   coderun, and a podman pull).

4. **Build + sign the verity UKI (optional, separate gate).** Set
   `[uki].verity_uki_build = true`. The build step runs `ukify build` measuring
   the composefs fs-verity digest. The artifact lands at
   `/usr/lib/modules/<kver>/mios-verity.efi` — **unsigned and not installed**.
   Sign it with a MOK already enrolled in the firmware, install it as a boot
   entry, and **boot-test it with the old entry still selectable**. Confirm a
   `bootc rollback` works before removing the fallback entry.

5. **Flip enforce — last, and only after 2–4 pass clean.** Uncomment the
   relevant kargs in `32-mios-ws7-uki.toml` (`fapolicyd.permissive=0`, and only
   if the signed UKI booted, `lockdown=confidentiality` / `rootflags=verity.require`),
   set `etc/fapolicyd/fapolicyd.conf` `permissive = 0`, rebuild, deploy.
   **Boot-test immediately.** If the box does not come up, `bootc rollback`
   from the bootloader to the prior (permissive) deployment.

6. **Verify enforce + the carve-out together.** Confirm services + the agent
   stack run, AND confirm a coderun dry-run still executes (the carve-out
   holds), AND confirm an arbitrary script dropped under `/home` or `/tmp`
   outside the sandbox is denied:
   ```bash
   echo 'echo nope' > /tmp/x.sh; chmod +x /tmp/x.sh; /tmp/x.sh   # should be denied
   ```

## Rollback / panic

- From the bootloader: pick the previous deployment (bootc keeps it staged).
- `bootc rollback` then reboot.
- Emergency disable without rebuilding: append `fapolicyd.permissive=1` (or
  `systemd.mask=fapolicyd.service`) at the bootloader edit prompt for one boot,
  then fix the rules and redeploy.

## Why default-off is deliberate, not incomplete

Per `aios-implementation-plan.md` §Appendix B, WS-7 is the single place where
"default on, debug live" is the wrong order. Everything here is reviewed and in
tree so the carve-out + observe posture already exist before anyone considers
enforce — but the brick-capable flips stay commented/`false` until the
procedure above passes on the actual image.

This is consistent with how the rest of MiOS treats brick-capable choices: the
heavy inference lanes (`mios-llm-heavy`, `mios-llm-heavy-alt`) are likewise gated
in `mios.toml` and stay inert until explicitly enabled, and `composefs_mode`
defaults to `"verity"` only where the underlying filesystem supports it. WS-7
extends that discipline to the boot path itself — the layer where a wrong flip
is unrecoverable rather than merely degraded.
