---
name: mios-environment
description: |
  MiOS host environment map. View this when the operator asks anything
  MiOS-specific: launch / open / find an app, inspect a service, edit
  config, schedule work, diagnose an issue. Prefer the documented
  helpers below over reconstructing the workflow from first principles
  — the helpers handle WSL interop, broker dispatch, perm escalation,
  + path translation that has bitten the agent before.
metadata:
  hermes:
    requires_tools:
      - run_sandboxed_code
      - read_file
---
<!-- AI-hint: Maps the MiOS host environment and provides a reference for agents to use specific helper tools (mios-find, mios-windows, mios-gui) to launch apps, manage services, and handle cross-platform execution.
     AI-related: /usr/share/mios/hermes/skills/mios-environment/SKILL.md., /etc/mios/cron-rules.toml, mios-environment, mios-find, mios-windows, mios-gui, mios-hermes-firstboot, mios-apps, mios-open-url, mios-pc-control -->

# MiOS environment — surface map

> _MiOS-managed: seeded by mios-hermes-firstboot from
> /usr/share/mios/hermes/skills/mios-environment/SKILL.md. Delete
> this marker to take ownership._

You are on a **MiOS host** — immutable Fedora bootc workstation, `/`
is a git working tree, the operator chats with you via OWUI as
**MiOS-Agent**. The prefilter already refined the prompt with a
reasoning template before you saw it.

## Operator says "launch X" / "open X" — the canonical chain

```
mios-find X            ← ~60ms; Voidtools Everything + Steam/Epic/GOG
                          libraries + Get-StartApps + Linux flatpak
                          inventory. Returns ONE line: a ready-to-
                          execute launch command.
<the line it printed>  ← broker-routes to operator session; window
                          appears + auto-centers + foregrounds.
```

Two commands. ~150 ms. If `mios-find` says "no match", `mios-apps
--filter X` once + ASK the operator where X lives — never recurse
the C: drive. Voidtools (via `mios-find`) already indexed it.

## Host-specific helpers

All on `$PATH` (`/usr/libexec/mios/` → `/usr/local/bin/` symlinks).
Paths the helpers consume read from `mios.toml [paths]` — you don't
memorise paths.

| Helper | Job |
|---|---|
| `mios-find <query>` | App / file / executable lookup, broker-safe |
| `mios-windows launch <name-or-path>` | Launch any Windows app/.exe (broker-routed, auto-centers). Accepts stdin: `echo PATH \| mios-windows launch -` (safe for paths with spaces/parens) |
| `mios-windows ps <pwsh>` | One-shot PowerShell on Windows (broker-routed) |
| `mios-windows cmd <cmdline>` | One-shot cmd.exe on Windows (broker-routed) |
| `mios-open-url <URL>` | Open URL in operator's browser |
| `mios-gui <flatpak-shortname>` | Launch a Linux GUI flatpak |
| `mios-pc-control <subcmd>` | Screenshot / click / type / window-list / window-focus / window-move / window-resize / **window-center** |
| `mios-pc-vision <png> <description>` | Vision-grounded click coordinates (qwen3-vl:4b) |
| `mios-doctor` | Health probe — run first when something's off |
| `mios-apps` | Full inventory of launchable things (Linux + Windows + games) |
| `mios-env-probe` | Hardware / services / models snapshot |
| `mios-restart <svc>` | Smart restart (knows Quadlet vs systemd) |
| `mios-ai-reset` | Wipe chat/session state for a clean slate |
| `mios-micro-llm` | Direct CLI to the always-on micro-LLM |
| `mios-build-status` / `mios-build-tail` | Build introspection |

## Background daemons (READ-only — they observe, they don't launch)

| Service | What it watches | State file |
|---|---|---|
| `mios-log-watcher.service` | journalctl PRIORITY=4+ batches | `/var/lib/mios/log-watcher/latest.json` |
| `mios-cron-director.service` | `/etc/mios/cron-rules.toml`, micro-LLM-gated | `/var/lib/mios/cron-director/state.json` |
| `mios-agent-nudger.service` | hermes journal for refusal patterns | `/var/lib/mios/agent-nudger/latest.json` |

Read these freely when the operator asks "what happened" / "anything
broken" / "what fired".

## Shared scratchpad

`/var/lib/mios/scratch/` (mode 1777, world rw+x) — drop notes,
hand-offs, cached lookups for downstream agents.

```
/var/lib/mios/scratch/sessions/<id>/   per-conversation
/var/lib/mios/scratch/handoffs/        structured agent-to-agent
/var/lib/mios/scratch/notes/           freeform
```

## Service map (what listens where)

```
hermes-agent.service             port key hermes      you (OpenAI gateway)
mios-delegation-prefilter        port key prefilter   prompt refinement + name rewrite + force-delegate
mios-open-webui.service          port key open_webui  OWUI browser UI
mios-searxng.service             port key searxng     privacy search (web tool backend)
mios-forge.service               :3000  Forgejo
mios-agents.service              :8800  VSCode-in-browser (replaces code-server)
mios-mcp.service                 -      Agent Context Service
```

## Canonical paths (FHS + bootc-compliant)

```
/usr/share/mios/                 vendor read-only (toml, ai/, skills/, modelfiles)
/etc/mios/                       operator overlay (mios.toml host override, secrets)
/var/lib/mios/                   state (hermes/, open-webui/, scratch/, ...)
/var/lib/mios/hermes/            $HERMES_HOME (config.yaml, SOUL.md, skills/, sessions)
/var/home/$MIOS_IDENTITY_USERNAME/.hermes/    operator CLI home (MIOS_IDENTITY_USERNAME from mios.toml [identity].username)
/run/mios-launcher/launcher.sock operator broker (cross-user dispatch)
/usr/libexec/mios/               vendor helpers (90+ scripts)
/usr/local/bin/mios-*            symlinks for agent $PATH discovery
```

## When unsure

1. `mios-doctor` (5s health probe)
2. Read the latest state file for the relevant daemon
3. `mios-apps --filter <name>` if launching
4. ASK the operator directly in your reply text

NEVER fabricate. If `mios-find` returns "no match", that's the answer —
don't pretend you launched it.
