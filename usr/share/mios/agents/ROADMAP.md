<!-- AI-hint: MiOS architectural documentation: MiOS Frontier — Roadmap (A2O War-Room).
     AI-related: /etc/mios/install.env, mios-frontier, mios-agents, mios-a2o, mios-code-server, mios-sync-env, mios-root, mios-agents.service -->

# MiOS Frontier — Roadmap (A2O War-Room)

> Scoped roadmap for the **mios-frontier** workflow: the Architect→Operator (A2O)
> tmux war-room that runs **inside the `mios-agents` code-server container**, in
> which an orchestrator agent delegates build/debug work to operator sub-agents,
> each in its own live lane, with humans watching/steering through the same
> code-server terminal (the "glass wall"). Companion to `ACTIVATION.md` (bring-up)
> and the repo-root `ROADMAP.md` / `tasks.jsonl`. Detailed tasks: the F- records in `tasks.jsonl`.
>
> **Honesty rule (inherited):** DONE = **active + live-fired** in the container,
> not "built + gated-off", not "done-by-code", not introspection-only. Anything
> shipped-but-inert is a gap, tracked as such.
>
> **Binding laws:** NO-HARDCODE (models / efforts / panes / ports flow from
> `mios.toml [frontier]`, never literals in the harness) · everything
> streams natively to every surface · credentials mounted at runtime, **never
> baked** · Law 6 (Quadlet `User=/Group=/Delegate`) · Claude-the-builder never
> live-launches — it extends the code paths; the operator opens the war-room.

---

## 1. What mios-frontier is

`mios-frontier` is a thin shim over `mios-a2o frontier`. `mios-a2o` is an
engine-aware terminal-muxer harness (dispatch / status / tail / capture / send /
repl / follow / doctor / selftest / frontier). The **frontier** profile lays out
a persistent tmux session as a **war-room**:

```
┌──────────────────────────┬─────────────────────────────┐
│                          │  LANE A  — sub-agent (live) │
│  ORCHESTRATOR            ├─────────────────────────────┤
│  (main panel, interactive)│  LANE B  — sub-agent (live) │
│                          ├─────────────────────────────┤
│                          │  MONITOR — all tasks/status  │
└──────────────────────────┴─────────────────────────────┘
```

The whole thing runs in **one container** (`mios-agents`) whose base image *is*
code-server, so the browser IDE and the tmux war-room share one filesystem and
one tmux socket — a human at the code-server terminal sees and steers exactly
what the agents see (the "glass wall"). `mios-agents` replaces the retired
`mios-code-server` (one IDE, no duplicate service).

**Engines** the harness can drive per role: `claude` (Claude Code CLI),
`agy` (Antigravity CLI → Gemini), `gemini` (Gemini CLI). Each is installed in the
image at a system path (`/usr/local/bin`) so the persistent `/home/coder` mount
cannot shadow it.

## 2. Target composition (operator spec)

| Role | Pane | Engine | Model | Reasoning effort |
|---|---|---|---|---|
| **Orchestrator** | main (left) | `claude` | Claude **Sonnet 5** (`claude-sonnet-5`) | high |
| **Sub-agent A** | right-top | `claude` | Claude **Opus 4.8** (`claude-opus-4-8`) | **xhigh** |
| **Sub-agent B** | right-mid | `agy` | **Gemini Flash 3.5** *(engine model id — verify)* | high |
| Monitor | right-bottom | — | — | — |

The orchestrator (Sonnet 5) runs interactively in the main panel and **delegates**
to the two sub-agent lanes (Opus 4.8, Gemini Flash 3.5) via `mios-a2o dispatch` /
the `lane-a` / `lane-b` convenience verbs; the lanes stream live; the monitor
shows all task state. Two `claude`-engine roles with **distinct models** (Sonnet 5
orchestrator vs Opus 4.8 sub-agent) is why the harness must key config on **role**,
not just engine.

## 3. Architecture & design principles

- **Role-based config, SSOT.** Each role = `{engine, model, effort}`, read from
  `MIOS_A2O_*` env, whose single documented baseline is the operator spec above,
  and whose real source of truth is `mios.toml [frontier]` bridged into the
  container env via `install.env`/`mios-sync-env`. No model/effort literal ever
  lives in the harness.
- **Reasoning effort is degrade-open.** Effort is passed via a per-engine flag
  *template* (`{e}` → the effort value); an empty template omits the flag, so an
  engine/CLI whose exact effort flag is unverified never breaks. Activation is a
  one-line SSOT change once the flag is confirmed in-container.
- **Orchestrator degrade-open.** If a role's engine binary is missing/unauthed,
  its pane falls back to a shell + `frontier-help` rather than a dead pane.
- **Credentials at runtime, never baked.** `agy` OAuth and Claude login are
  entered/mounted at run time (`podman exec -it … agy` / a mounted
  `~/.claude/.credentials.json` secret). The image ships zero secrets.
- **Glass wall.** One container, one tmux socket, one workspace (`/mnt/mios-root`
  = the deployed root, so the agents develop MiOS from within itself).
- **Everything streams.** Each lane's activity (dispatch, tool use, output,
  status) is live in its pane and tee'd to a per-task log; the war-room's state is
  observable via `status` / `follow` / `tail` / `capture` without attaching.
- **Least privilege.** The Quadlet honors Law 6 where the base image allows;
  `MIOS_A2O_AUTO=1` (auto-approve operator tools) is confined to this container
  and requires explicit operator authorization.

## 4. Phases

**Phase 0 — Super-container baked ✅ (verify live).** `mios-agents` image
(Containerfile: code-server + tmux + Claude CLI + agy + `mios-a2o` + `mios-frontier`),
`[containers.mios-agents.*]` SSOT, pod membership, tmpfiles `/var/lib/mios/agents`,
bound-images symlink, `mios-agents.service` enabled (replacing `mios-code-server`).
*Status: assets present + service enabled; confirm the image builds and the
container serves the IDE + a working `mios-a2o doctor` in-container.*

**Phase 1 — Role-based harness (task_3166, task_3168). Built.** Replaced the per-engine-only
model vars with `{orchestrator, lane_a, lane_b}` roles each carrying
engine+model+effort; `exec_line` accepts model+effort; the degrade-open
effort-flag template is live (`effort_flag()`, `mios-a2o:66-72`). *Status: shipped
+ live-verified.*

**Phase 2 — Frontier profile = orchestrator + sub-agent lanes (task_3167, task_3170). Built.**
`cmd_frontier` runs the orchestrator engine interactively in the main pane
(model+effort pinned, degrades to shell if missing); lanes A/B follow the two
sub-agent roles; monitor unchanged. `lane-a`/`lane-b` convenience dispatch
resolves to the sub-agent role config. Pane titles show `engine:model (effort=…)`.
*Status: shipped + live-verified (task_3177's `FRONTIER-LAYOUT` selftest asserts the
4-pane layout engine-free).*

**Phase 3 — SSOT + env-bridge (task_3169, task_3173). Built.** `[frontier]` lives in
`mios.toml`; `tools/lib/userenv.sh` slot-pairs `frontier.*` → `MIOS_A2O_*`
(including `lane_a_role`/`lane_b_role`); `system-sync-env.sh` emits the full
`MIOS_A2O_*` block into `/etc/mios/install.env`; `mios-agents.service` forwards
every var via `--env` into the container. Model ids SSOT'd (task_3173 done —
Sonnet-5/Opus-4.8 confirmed; Gemini Flash 3.5 id log-verified as
`"Gemini 3.5 Flash (High)"`, the exact `agy models` display string, via an
`agy --log-file` resolution trace — bare slugs fail resolution and silently
fall back to Medium effort).

**Phase 4 — Reasoning-effort activation (task_3175). Built.** `claude`'s effort flag
confirmed in-container = `--effort {e}` (levels low|medium|high|xhigh|max),
activated as `mios.toml [frontier].claude_effort_flag`. `agy` needs no separate
flag — effort is baked into the model display name itself (see Phase 3), so
`agy_effort_flag` stays empty by design. `gemini` CLI isn't installed, so its
template stays empty (degrade-open, untested). *Status: resolved, not blocked.*

**Phase 5 — Full-surface streaming (task_3176). MVP built, activation seam in
progress.** `[frontier].stream_to_reasoning` (default off, degrade-open) +
`stream_path` gate a JSONL sink `cmd_dispatch`'s runner appends per-task
start/finish transitions to; `sse.py`'s `_frontier_stream_events()` folds those
into the same `mios_status` reasoning-channel emission OWUI/CLI/Discord already
read. *Status: code path shipped; the container write-permission activation seam
(confirming the mount can write `MIOS_FRONTIER_STREAM_PATH` once flipped on) is being
finished by the code lane before the flag defaults to on.*

**Phase 6 — Identity, security, hardening (task_3172, task_3174, task_3177). Built.**
`mios-agents` in the security allowlist ✅; credential-mount flow verified
(runtime OAuth/`/login`, `/home/coder` persists across restarts, zero secrets
baked) ✅; `MIOS_A2O_AUTO` confinement unchanged; Law-6 posture unchanged;
selftest asserts the role/frontier layout (task_3177) ✅. *Status: complete.*

**Phase 7 — Advanced orchestration (task_3184+). Design sketches written, not yet
coded.** Parallel fan-out beyond two lanes, per-task checkpointing/resume,
orchestrator↔sub-agent structured hand-off (A2A), cost/turn budgets, and a
"completeness critic" lane. Concrete per-item designs (files to touch,
degrade-open activation path) now live in the F- records of `tasks.jsonl` ("Phase-7 design
sketches" section — including task_3183 (Lane-B `agy`→`claude` degrade-open
fallback while the Gemini account quota is exhausted, in-progress) and the
now-resolved dispatch-vs-nested-tool-loop question (dispatch is the shipped
and preferred shape; nested tool-loop recorded as the Phase-7 alternative).
*Status: future — designs ready to pick up.*

## 5. Relationship to the MiOS roadmap

mios-frontier is the human-facing face of the A2O program: the repo-root
`tasks.jsonl` **task_10** (war-room rework) is the umbrella; the sub-agent
visibility work (FV-*) and constrained-tool-calling (task_112) apply to the lanes;
the native-typed-launch-args mandate (task_120) governs how the harness exposes its
own verbs. This file decomposes the war-room into shippable frontier tasks.

## 6. Open questions / verify points (do NOT guess — confirm in-container)

All prior open questions in this section are now resolved; kept here (struck
through) as the historical record per the honesty rule.

- ~~**Gemini Flash 3.5 model id** for the `agy`/`gemini` engine (`--model`
  value).~~ **Resolved (task_3173):** `"Gemini 3.5 Flash (High)"` — the exact
  `agy models` display string, log-verified via `agy --log-file` resolution
  trace. Bare slugs (`gemini-3.5-flash`, etc.) fail resolution and silently
  fall back to Medium effort, so the display string is the only accepted value.
- ~~**Per-CLI reasoning-effort flag** for `claude`, `agy`, `gemini`.~~
  **Resolved (task_3175):** `claude` = `--effort {e}` (low|medium|high|xhigh|max),
  confirmed in-container and activated in `mios.toml [frontier]`. `agy` has no
  separate flag — effort is encoded in the model display name (see above), so
  `agy_effort_flag = ""` is correct by design. `gemini` CLI is not installed;
  its template stays empty (degrade-open, untested).
- ~~**Port**: `MIOS_PORT_AGENTS` — ACTIVATION cites `8801`, the live dashboard
  shows `8800`; reconcile the SSOT.~~ **Resolved (task_3178):** `mios-agents` reuses
  the retired `mios-code-server` port — one IDE, no duplicate service. SSOT is
  `[ports].code_server = 8800`; `MIOS_PORT_AGENTS` is retired.
- ~~Whether the orchestrator should **dispatch** to lanes or run a nested
  tool-loop that spawns them (Phase 2 vs Phase 7 shape).~~ **Resolved:** the
  shipped shape is fire-and-forget **dispatch** (`cmd_lane`/`cmd_dispatch` →
  tmux windows tee'd to logs) — preferred for glass-wall human observability,
  engine-agnosticism (no uniform tool-calling protocol exists across
  `claude`/`agy`/`gemini`), degrade-open failure isolation, and surviving an
  orchestrator restart (task state lives on disk, not in-process). A nested
  tool-loop is recorded as the Phase-7 alternative in `tasks.jsonl` ("Phase-7
  design sketches" section, worth revisiting only if the three engines gain a
  uniform structured tool-calling protocol.
