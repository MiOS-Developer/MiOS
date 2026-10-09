<!-- AI-hint: MiOS architectural documentation: mios-agents — Architect→Operator (A2O) super-container.
     AI-related: /etc/mios/install.env, /usr/libexec/mios/mios-agents-firstboot.sh, mios-agents, mios-a2o, mios-agents-dev, mios-forge-mirror, mios-code-server, mios-agents-firstboot, mios-root, mios-sync-env -->

# mios-agents — Architect→Operator (A2O) super-container

One image = **code-server** (browser IDE / glass wall) + **tmux** war room +
**Claude Code CLI** + **agy** (Antigravity CLI → Gemini) + the **`mios-a2o`**
muxer. Claude scaffolds; an operator engine finishes the last 20% inside a tmux
session; humans watch/steer via the code-server terminal (same container, same
tmux socket). Credentials are **mounted/entered at runtime — never baked**.

Files in this dir:
- `Containerfile` — the image source (base = code-server; adds tmux/Claude/agy/harness).
- `mios-a2o` — the engine-aware war-room muxer (`dispatch|status|tail|capture|send|repl|doctor|selftest`).
- `mios-agents-dev.sh` — DEV build+run helper (rootless podman, IDE on `:8801`).
- `mios-forge-mirror.sh` — Forgejo pull-mirror of the GitHub repo (owner/port from SSOT).

## DEV validation (do this first — you launch it)

```bash
bash usr/share/mios/agents/mios-agents-dev.sh          # build + run in MiOS-DEV
podman exec -it mios-agents-dev agy                    # sign in the Gemini operator
# IDE: http://localhost:8801 (pw = mios) → terminal runs `mios-a2o`
```

## Phase B — what shipped

> Not a Quadlet — `mios-agents` is a hand-written systemd unit (like
> `mios-code-server` before it), not `[containers.*]` SSOT + generated Quadlet.
> This section describes the ACTUAL wiring, not a proposal.

**1. Port — reuses the retired `mios-code-server` port.** `mios-agents` replaces
`mios-code-server` (one IDE, no duplicate service), so it binds the SAME port
rather than registering a new one. SSOT is `[ports].code_server = 8800` in
`usr/share/mios/mios.toml`; the unit's `Exec` uses `${MIOS_PORTS_CODE_SERVER}`
(in-unit default `Environment=MIOS_PORTS_CODE_SERVER=8800`, since systemd does
NOT expand bash `${VAR:-default}` in `ExecStart`). There is no
`MIOS_PORT_AGENTS` — that name is retired.

**2. The unit** — `usr/lib/systemd/system/mios-agents.service`:
```ini
[Service]
Type=simple
Environment=MIOS_PORTS_CODE_SERVER=8800
Environment=MIOS_IDENTITY_DEFAULT_PASSWORD=mios
EnvironmentFile=-/etc/mios/install.env
ExecStartPre=/usr/libexec/mios/mios-agents-firstboot.sh
ExecStart=/usr/sbin/podman run --replace --name mios-agents \
  --network=host \
  --env PASSWORD=${MIOS_IDENTITY_DEFAULT_PASSWORD} \
  --env MIOS_A2O_ENGINE=agy \
  --env MIOS_A2O_WORK=/mnt/mios-root \
  --env MIOS_FRONTIER_ORCH_ENGINE=${MIOS_FRONTIER_ORCH_ENGINE} \
  --env MIOS_FRONTIER_ORCH_MODEL=${MIOS_FRONTIER_ORCH_MODEL} \
  --env MIOS_FRONTIER_ORCH_EFFORT=${MIOS_FRONTIER_ORCH_EFFORT} \
  --env MIOS_FRONTIER_LANE_A_ENGINE=${MIOS_FRONTIER_LANE_A_ENGINE} \
  --env MIOS_FRONTIER_LANE_A_MODEL=${MIOS_FRONTIER_LANE_A_MODEL} \
  --env MIOS_FRONTIER_LANE_A_EFFORT=${MIOS_FRONTIER_LANE_A_EFFORT} \
  --env MIOS_FRONTIER_LANE_B_ENGINE=${MIOS_FRONTIER_LANE_B_ENGINE} \
  --env MIOS_FRONTIER_LANE_B_MODEL=${MIOS_FRONTIER_LANE_B_MODEL} \
  --env MIOS_FRONTIER_LANE_B_EFFORT=${MIOS_FRONTIER_LANE_B_EFFORT} \
  --env MIOS_FRONTIER_CLAUDE_EFFORT_FLAG=${MIOS_FRONTIER_CLAUDE_EFFORT_FLAG} \
  --env MIOS_A2O_AGY_EFFORT_FLAG=${MIOS_A2O_AGY_EFFORT_FLAG} \
  --env MIOS_A2O_GEMINI_EFFORT_FLAG=${MIOS_A2O_GEMINI_EFFORT_FLAG} \
  --volume /:/mnt/mios-root:rw,rslave \
  --volume /var/lib/mios/agents:/home/coder:rw \
  localhost/mios-agents:latest \
  --bind-addr 0.0.0.0:${MIOS_PORTS_CODE_SERVER} /mnt/mios-root
```
`EnvironmentFile=-/etc/mios/install.env` (the `mios-sync-env`-written
mios.toml→env bridge) overrides the in-unit defaults when present and supplies
every `MIOS_A2O_*` role var, which the `--env` flags forward straight into the
container.

**3. Image build** — `ExecStartPre=/usr/libexec/mios/mios-agents-firstboot.sh`
builds `localhost/mios-agents:latest` from
`usr/share/mios/agents/Containerfile`. Idempotent: no-op if the image exists and
is newer than the Containerfile; rebuilds when the image is missing OR the
Containerfile's mtime is newer than the image's `Created` timestamp (so a
Containerfile edit doesn't pin a stale image forever).

**4. Preset** — `usr/lib/systemd/system-preset/90-mios.preset`:
```
enable mios-agents.service
disable mios-code-server.service
```
`mios-agents` is the one running IDE; `mios-code-server`'s `[containers.*]`
definition stays in `mios.toml` but inert (no duplicate service).

**5. Persistence** — `/var/lib/mios/agents:/home/coder:rw` keeps `agy`/`claude`
logins and war-room state across container restarts; the `/:/mnt/mios-root`
bind-mount is the live deployed root, so the agents develop MiOS from within
itself. On first start, `mios-agents-firstboot` also seeds the portrait
code-server profile at
`/home/coder/.local/share/code-server/User/settings.json`; existing user
settings remain authoritative.

**6. Role SSOT** — `[frontier]` in `usr/share/mios/mios.toml` is the single
source of truth for orchestrator/lane-A/lane-B engine+model+effort. `mios-sync-env`
(`usr/libexec/mios/system-sync-env.sh`) bridges `frontier.*` keys to the
`MIOS_A2O_*` names (via `tools/lib/userenv.sh`'s slot table) and writes them into
`/etc/mios/install.env`, which the unit's `EnvironmentFile=-` picks up and the
`ExecStart` `--env` flags forward into the container. Editing `[frontier]` +
running `mios-sync-env` + restarting the service changes the war-room roles —
no code change needed.

## Credentials (runtime, never baked)
- **agy (Gemini):** `podman exec -it mios-agents agy` → interactive OAuth (you reserve this).
- **Claude:** either a mounted `~/.claude` (see `mios-agents-dev.sh`, which bind-mounts the host's
  `~/.claude` and `~/.claude.json` into the container's `/home/coder`), or
  `podman exec -it mios-agents claude` → `/login` inside the container.
- **Persistence:** both agy and claude logins land under `/home/coder` in the container, which the
  unit mounts from `/var/lib/mios/agents:/home/coder:rw` (declared via tmpfiles, `d /var/lib/mios/agents`
  in `usr/lib/tmpfiles.d/mios-agents.conf`) — so logins survive container restarts.
- **Auto-approve operator:** dispatch with `MIOS_A2O_AUTO=1` (adds `--dangerously-skip-permissions`) — confined
  to this container. Requires your explicit authorization.

## Lane B status (live)
The real Gemini lane (`agy`) is currently **account-quota-blocked** — `agy`
authenticates fine and `agy models` lists models, but `agy --print`/`-p` returns
empty stdout because the backing Gemini account has hit its quota (HTTP 429
`RESOURCE_EXHAUSTED`, "Individual quota reached"; resets ~2026-07-07). Operator
options: wait for the reset, or upgrade the `agy`/Gemini subscription. Until
resolved, Lane-B finalize work runs on the `claude` fallback engine. See
`tasks.jsonl` task_3188 (root cause) and task_3189 (hardening the `mios-a2o` dispatch so a
silent agy failure is never reported as DONE).
