<!-- AI-hint: Manual pages distilled from the source comments of system, sanitized, each passage anchored to the comment it came from. -->

# system

### Image-build (automation/72-hermes-agent.sh) installs the...

Image-build (automation/72-hermes-agent.sh) installs the patched,
self-hosted React SPA into the venv's hermes_cli/web_dist. That's
where `hermes dashboard` looks by default, so no HERMES_WEB_DIST
override is needed in the happy path. If the SPA build was skipped
(no node/npm, network blocked, etc.), the dashboard exits early --
/usr/share/mios/hermes-agent/web_dist_stub holds the curl-recipes
fallback page; set the env var below to switch to backend-only mode:
  Environment=HERMES_WEB_DIST=/usr/share/mios/hermes-agent/web_dist_stub
Don't try to launch a browser in a headless service context.

<!-- mios-src:aa875fbee5e9 from usr/lib/systemd/system/hermes-dashboard.service:27-35 -->

### Enable the /chat tab (xterm.js + /api/pty WebSocket)....

Enable the /chat tab (xterm.js + /api/pty WebSocket). Upstream
default is OFF for security. Here we enable + override the spawned
child via HERMES_PTY_SHELL (a MiOS-side patch in
hermes_cli/web_server.py, installed by automation/72-hermes-agent.sh).
Result: dashboard /chat shows a real bash login prompt rendered by
xterm.js in the browser. Loopback bind + per-session-token still
gate the endpoint -- anyone who can read the dashboard URL+token
gets a shell as the mios-hermes uid (the service's User=).

<!-- mios-src:81881cba1533 from usr/lib/systemd/system/hermes-dashboard.service:38-45 -->

### Localhost bind by default. The operator's browser (Windows...

Localhost bind by default. The operator's browser (Windows side) hits
this via WSL2 portproxy; LAN exposure would require --insecure +
firewall opening, not enabled here.

<!-- mios-src:ac48f500d816 from usr/lib/systemd/system/hermes-dashboard.service:57-59 -->

### usr/lib/systemd/system/hermes-worker.service The MiOS...

/usr/lib/systemd/system/hermes-worker.service

The MiOS Hermes WORKER (P1, operator 2026-06-19). A SECOND `hermes gateway
run` instance, fully ISOLATED from the live Discord gateway on the `hermes` port:
  * SEPARATE HERMES_HOME=/var/lib/mios/hermes-worker => its own gateway.pid /
    gateway.lock / gateway_state.json / state.db / kanban.db / config.yaml.
    No shared-DB WAL contention with the `hermes`-port instance.
  * API_SERVER_PORT=8643 (the LOAD-BEARING bind var -- `PORT` is inert; Hermes
    reads API_SERVER_PORT and otherwise binds its built-in DEFAULT_PORT).
  * NO discord.env / NO DISCORD_BOT_TOKEN => the Discord adapter never calls
    _acquire_platform_lock('discord-bot-token', ...), so the host-global
    gateway-locks/discord-bot-token-*.lock held by the `hermes`-port gateway is never
    contended (no SIGTERM flap). Discord stays the EXCLUSIVE job of that gateway.
  * NO --replace: the worker's HERMES_HOME-scoped pidfile is its own; the
    `hermes`-port gateway's eviction scan is profile/HERMES_HOME-scoped (only --all
    crosses profiles, which is not used) so neither instance touches the other.

This worker is the WORKER-DISPATCH target of [agents.hermes].endpoint in
mios.toml (repointed from the heavy lane to :8643 in P1). It does its OWN heavy-lane
inference (mios-heavy, port key `vllm`) so it never relays to the `agent_pipe` port -- no recursion.

<!-- mios-src:0fa4044ac17b from usr/lib/systemd/system/hermes-worker.service:4-23 -->

### COMPLETION CAP (operator 2026-06-19 P1 fix): mios-heavy...

COMPLETION CAP (operator 2026-06-19 P1 fix): mios-heavy max_model_len=65536; with
NO cap hermes lets the completion default to the full 65536, so input+completion
overflows -> HTTP 400 "token count exceeds maximum context". Cap the per-call
output so input has room (8192 leaves ~57k for input; enough for the reasoning
model's reasoning_content + answer). HERMES_MAX_TOKENS wins over model.max_tokens.

<!-- mios-src:e83023f8a4a8 from usr/lib/systemd/system/hermes-worker.service:54-58 -->

### Enforce world-readable perms on /usr/lib/containers/storage...

Enforce world-readable perms on /usr/lib/containers/storage so that
unprivileged podman / flatpak / etc. callers can read the additional-
image-store baked into the OCI image.

Why this exists: the bake step in /Containerfile chmod's the store at
OCI build time (commit a85925d, May 2026), but operators on stale
pre-fix OCI images don't have that fix in their image. The result is
the recurring "configure storage: open ...overlay-images/images.lock:
permission denied" symptom that breaks flatpak shims, the operator's
`podman info`, and any GUI launch chain. Operator-confirmed regression
2026-05-15 (post-fresh-install on a stale image).

This unit runs early in boot (Before=podman-restart.service +
Before=hermes-agent.service so anything that touches the store sees
correct perms) and is idempotent: chmod -R go+rX is a no-op on
already-correct dirs.

<!-- mios-src:bc6dcdce3f39 from usr/lib/systemd/system/mios-additionalimagestores-perms.service:4-19 -->

### The venv python is baked into the FINAL image, but on the...

The venv python is baked into the FINAL image, but on the podman-MiOS-DEV substrate it is
built at first boot by mios-ai-firstboot. Skip (rather than 203/EXEC-fail) until it exists,
so the unit never lands in a "failed" state during the firstboot venv-build window; firstboot
`systemctl restart --no-block`s it once the venv lands (R8 still holds: no After= edge here,
so no blocking on the multi-GB model download).

<!-- mios-src:bf0858246578 from usr/lib/systemd/system/mios-agent-pipe.service:19-23 -->

### Run with the mios-hermes supplementary group so the service...

Run with the mios-hermes supplementary group so the service can read
/etc/mios/hermes/api.env (mode 0640, group mios-hermes) and load the
backend API_SERVER_KEY. Without it, _BACKEND_KEY stays empty and KEYLESS
direct callers (Firefox Smart Window, curl, MCP) reach Hermes with no auth
and get 401 -- only the OWUI path worked because it forwards the operator
token. The agent-pipe fronts Hermes, so sharing its key group is intended.

<!-- mios-src:424b25023a25 from usr/lib/systemd/system/mios-agent-pipe.service:30-35 -->

### SSOT chain

SSOT chain: every operator-tunable knob sources from mios.toml via
/etc/profile.d/mios-env.sh, which exports MIOS_* into the service
environment. The service code reads MIOS_PORTS_AGENT_PIPE +
MIOS_AGENT_PIPE_BACKEND + MIOS_DB_URL etc. as os.environ overrides.

<!-- mios-src:900e3d0db002 from usr/lib/systemd/system/mios-agent-pipe.service:39-42 -->

### WS-10 (operator 2026-06-05 "EVERYTHING IS LLAMA.CPP"): the...

WS-10 (operator 2026-06-05 "EVERYTHING IS LLAMA.CPP"): the agent-pipe
reasons DIRECTLY on mios-llm-light (port key `llm_light`) instead of via a Hermes hop. All
inference is on the dGPU via llama.cpp.
2026-06-15 (Claude): model fleet swapped gemma4:12b -> granite4.1:8b (the served
brain on mios-llm-light; see mios-llm-light.yaml + mios.toml:4061). gemma4:12b is
NO LONGER SERVED -> any role still naming it 404s on llama-swap ("no model id").
Align EVERY pipeline role to the ONE resident served brain (granite4.1:8b): set the
BASE default MIOS_STACK_MODEL (which _MICRO/DCI/SWARM/refine/polish/planner inherit
via server.py) + the explicit BACKEND/SWARM/MICRO names. ONE resident model = no
llama-swap thrash. Override per-deployment in /etc/mios/agent-pipe.env.
WS-0B: the agent-pipe reasons DIRECTLY on the light lane (NOT fronting Hermes
on the `hermes` port). Opt in by FLAG -- server.py composes the URL from _LIGHT_BASE, so the
port lives in ONE place ([ports].llm_light), not a literal here. MICRO/ROUTER/
LLM_CPU/PLANNER/REFINE/POLISH endpoints ALSO derive from _LIGHT_BASE now and
are no longer pinned in this unit.

<!-- mios-src:fcd7ab5afd0f from usr/lib/systemd/system/mios-agent-pipe.service:50-64 -->

### llama.cpp/mios-llm-light (port key `llm_light`) is OpenAI /v1 ONLY (no...

llama.cpp/mios-llm-light (port key `llm_light`) is OpenAI /v1 ONLY (no legacy /api/chat, /api/ps)
and 400s on tool_choice=required. Register it so the pipe treats it as llamacpp:
skip forced tool_choice (no 400) + use the /slots KV-paging path. No bare port
literal in routing code -- these are the env-SSOT hint lists.
llama.cpp b9519 (the dGPU mios-llm-light) ACCEPTS tool_choice=required (verified) --
only the older iGPU b9305 (:11436) rejects it. So `llm_light` must NOT be in the
no-tool-choice list, or the pipe drops/auto's tool_choice and gemma4 NARRATES
the call instead of executing it. Keep `llm_light` in KV_PAGING (it does /slots).

<!-- mios-src:14a9c500f03c from usr/lib/systemd/system/mios-agent-pipe.service:71-78 -->

### WS-0B

WS-0B: ROUTER + the CPU-offload light-lane (MIOS_LLM_CPU_ENDPOINT) both now
default to _LIGHT_BASE in server.py (mios-llm-light) -- no per-endpoint pin here.
Override either via /etc/mios/agent-pipe.env only if a deployment splits lanes.
Phase A.1 -- planner runs on the dGPU/CUDA lane (the retired Ollama lane). Falls back to
whatever lane is reachable if that lane is down.
Model: mios-agent -- the canonical "MiOS AI" model (Modelfile FROM
qwen3.5:4b as of 2026-05-22 consolidation; was gemma4:e4b). ONE light
brain now serves plan + refine + polish with the full MiOS agent SYSTEM
(act-don't-narrate / right-tool-for-goal / SSOT mios-os-control surface,
ENGLISH-default, no hardcodes). The planner emits a single structured
verb/recipe DAG; if it ever empties, DAG mode degrades to the backend
proxy. Override via /etc/mios/agent-pipe.env.
History: qwen2.5-coder:7b -> mios-planner(gemma4) -> mios-agent(gemma4)
-> mios-agent(qwen3.5:4b, consolidated brain) 2026-05-22.
WS-0B: PLANNER endpoint+model both derive from server.py (_LIGHT_BASE +
MIOS_STACK_MODEL) -- no per-role pin here.
WS-A3 de-rot: the legacy DB (:8000) is RETIRED -> the agent DB is Postgres+pgvector
(MIOS_PG_* / mios-pgvector). The former MIOS_DB_URL/USER/PASS/NS/DB legacy env
is removed (the pg client reads MIOS_PG_* + the pgvector quadlet creds).
Phase C.3 -- this service signs agent-plane writes (pgvector + A2A) as agent-pipe.

<!-- mios-src:7a803109b31e from usr/lib/systemd/system/mios-agent-pipe.service:82-101 -->

### refine (input prompt-enhancement -> the structured plan...

refine (input prompt-enhancement -> the structured plan every downstream node
consumes; THE quality lever) + polish (final-answer shaping WITH the MiOS
persona) both run on the ONE resident served brain via the agent-pipe lane
resolver (mios-llm-light; the retired qwen3.5:4b/qwen3:1.7b dGPU Ollama-lane pins are
GONE). WS-0B: their endpoint AND model now derive from server.py (_LIGHT_BASE +
MIOS_AI_MODEL, the one owned keys) -- NEITHER is pinned in this unit anymore. The
CONCURRENT swarm secondaries still fan out to OTHER lanes at the same dispatch
step ([[mios_heterogeneous_parallel_dispatch]]). Override via
/etc/mios/agent-pipe.env only to split a lane.
Phase D.5 -- refine + polish timeouts on CPU-bound dev hardware.
Real ROCm/CUDA finishes the LLM call in <1s; WSL CPU takes 15-30s
for the same prompt. Generous timeout works on both.

<!-- mios-src:582fc52f8dd4 from usr/lib/systemd/system/mios-agent-pipe.service:103-114 -->

### Agent self-memory recall (operator 2026-06-20): inject the...

Agent self-memory recall (operator 2026-06-20): inject the agent's OWN durable
self-edited facts (remember/memory_update -> agent_memory, embed-on-write) into
context so a "what did I tell you" / "what is my X" turn RETRIEVES the stored fact
instead of replying "I don't have that" (the WRITE half worked; the READ half was
default-OFF only to keep the hot path byte-identical until flipped). Safe: the
agent's OWN facts, same injection class as knowledge recall, NOT env-detection (the
no-context-injection rule is scoped to env discovery). RLS fail-closed still applies
under rls_mode=enforce; default rls_mode=off -> unchanged.

<!-- mios-src:946ed1dfa4a2 from usr/lib/systemd/system/mios-agent-pipe.service:118-125 -->

### Reuse hermes-agent's venv interpreter for...

Reuse hermes-agent's venv interpreter for fastapi/uvicorn/httpx/
starlette -- they're already installed there. Cleaner option for a
bootc deploy is `dnf install python3-fastapi python3-uvicorn` +
/usr/bin/python3 (operator-confirmed 2026-05-18: python3-fastapi is
in updates/, python3-httpx already installed). Switching to dnf
packages is the v1 follow-up; for the scaffold commit we ride the
hermes-agent venv to skip a package-list bump in the same change.

<!-- mios-src:7471f670d81d from usr/lib/systemd/system/mios-agent-pipe.service:128-134 -->

### The script reads MIOS_LLAMACPP_BAKE_MODELS (the GGUF...

The script reads MIOS_LLAMACPP_BAKE_MODELS (the GGUF download spec) +
MIOS_AI_* from the env bridge. Without this, a fresh systemd boot has an
EMPTY environment -> bake_models reads empty -> "GGUFs not baked" -> the
llm-light lane stays inert forever. The leading '-' makes it optional so
the unit still starts (and retries) if the bridge isn't generated yet.
install-robustness 2026-06-21.

<!-- mios-src:d59660636efe from usr/lib/systemd/system/mios-ai-firstboot.service:26-31 -->

### First retry shortly after boot, then every 10min while the...

First retry shortly after boot, then every 10min while the service sits
inactive and the sentinel is still absent. Persistent catches up a missed
window across a shutdown. The timer OWNS retry now (the .service no longer
carries Restart=on-failure / StartLimitBurst).

<!-- mios-src:c68f826bb9fe from usr/lib/systemd/system/mios-ai-firstboot.timer:14-17 -->

### Needs the agent-pipe up

Needs the agent-pipe up: mios-gen-role-system reads the LIVE /v1/verbs catalog
and mios-a2a-discover probes A2A AgentCards. avahi-daemon backs the mDNS browse +
advertise (FED-G5). ALL degrade-open, so a transient miss (or no avahi at all)
never wedges anything -- hence Wants, never Requires.

<!-- mios-src:7c82b103f88f from usr/lib/systemd/system/mios-aios-refresh.service:6-9 -->

### The 2026-06-05 install failure was a set -euo pipefail trap...

The 2026-06-05 install failure was a set -euo pipefail trap in the script
(a bare `var=$(timeout ... powershell ...)` exits the shell when the pipeline
returns non-zero) -- fixed at the root in mios-cdi-detect itself, so the unit
runs clean with no ExecStart=- mask. All vendor branches are best-effort guarded.

<!-- mios-src:062b6532bdbe from usr/lib/systemd/system/mios-cdi-detect.service:14-17 -->

### usr/lib/systemd/system/mios-daemon.service MiOS...

/usr/lib/systemd/system/mios-daemon.service

MiOS consolidated micro-LLM daemon. Replaces three predecessors
(mios-log-watcher + mios-cron-director + mios-agent-nudger) with
ONE process that subscribes to journald once, holds a single
qwen3:0.6b-cpu client (keep_alive=-1 forever, num_gpu=0 CPU-only
per Law 7 OFFLINE-FIRST + "always-on agentic OS"), and dispatches
the three handlers off a single event stream. Writes a unified
/var/lib/mios/daemon/state.json the OWUI mios_sidecar Filter polls.

Operator directive 2026-05-17: "ALL to be consolidated to one
mios daemon/agent" + "keep_alive should be TRUE for a TRULY
Agentic OS--MiOS!"

<!-- mios-src:246a91740e67 from usr/lib/systemd/system/mios-daemon.service:4-16 -->

### var/lib/mios/daemon = the daemon's own state (state.json...

/var/lib/mios/daemon = the daemon's own state (state.json, launch_failures).
/var/lib/mios/scratch = the SHARED cross-agent blackboard the task_collector
drops agent-nudges into for other agents to read (operator 2026-05-24: under
ProtectSystem=strict it was read-only, so task_collector EROFS-failed writing
agent-nudges.md -- the nudge feature was silently dead).

<!-- mios-src:70a087601bb9 from usr/lib/systemd/system/mios-daemon.service:46-50 -->

### DO NOT use Before=getty.target -- getty.target is...

DO NOT use Before=getty.target -- getty.target is WantedBy=multi-user.target,
so combining After=multi-user.target with Before=getty.target creates an
ordering cycle:
  multi-user.target -> mios-dashboard-issue -> getty.target -> multi-user.target
systemd breaks the cycle by deleting getty.target's start job, which means
NO console getty spawns at boot. The 2-minute OnBootSec timer + getty's
Restart=always pick up the issue.d snippet within minutes of boot anyway.

<!-- mios-src:0e0f102267d0 from usr/lib/systemd/system/mios-dashboard-issue.service:17-23 -->

### Ensure MiOS service ports are open in firewalld at every...

Ensure MiOS service ports are open in firewalld at every boot.

Why this exists: automation/44-firewall-ports.sh writes the firewalld
zone XML at OCI build time via firewall-offline-cmd. On stale OCI
images (pre-2026-05) OR when the install-time script didn't run / the
XML didn't persist, firewalld comes up with no ports open and ALL
Windows->WSL bridging silently times out (operator-confirmed
regression 2026-05-15: Open WebUI/Hermes/Cockpit/SearXNG inaccessible
post-reinstall; firewall-cmd --list-ports returned empty; adding the
ports manually instantly restored all 4 services).

This unit runs at every boot and is idempotent: --add-port on a port
that's already open is a no-op. No-ops cleanly when firewalld is
absent (ConditionPathExists) or inactive.

<!-- mios-src:326262c3952a from usr/lib/systemd/system/mios-firewall-ports.service:4-17 -->

### Ports

Ports: forge_http=Forge, open_webui=OWUI, code_server=code-server, hermes=Hermes-Agent,
       searxng=SearXNG, cockpit=Cockpit, hermes_dashboard=Hermes-Dashboard,
       llm_light=LLM-Light, pgvector=pgvector, cockpit_link=Cockpit-link, adguard_ui=AdGuard UI, 53=AdGuard DNS.
(crawl4ai :11235 removed 2026-05-24: the crawl engine is now a LOOPBACK-only
 venv service -- mios-crawl4ai.service binds 127.0.0.1, never LAN-exposed.)
AdGuard DNS needs BOTH 53/tcp and 53/udp (UDP is the normal query path).

<!-- mios-src:5661a9f13594 from usr/lib/systemd/system/mios-firewall-ports.service:27-32 -->

### usr/lib/systemd/system/mios-flatpak-init.service Apply...

/usr/lib/systemd/system/mios-flatpak-init.service

Apply MiOS's system-wide flatpak override policy at first boot
(XDG dir grants so every flatpak can read+write the operator's
Documents / Pictures / Videos / Downloads / Music / Desktop /
Public). One-shot; idempotent on re-runs.

Operator directive 2026-05-15: agent-installable flatpaks should
Just Work against /var/home/mios/* userspace folders by default,
without per-app override gymnastics. /var/lib/flatpak/overrides/
global is the right surface (mutable on bootc, survives image
upgrades, operator-editable via sudo flatpak override --system).

<!-- mios-src:7099d0cface4 from usr/lib/systemd/system/mios-flatpak-init.service:4-15 -->

### Hardening

Hardening: this service writes to a small set of paths plus calls
'podman exec' against the running mios-forge container. RestrictNamespaces
and RestrictAddressFamilies were tried but break Podman's CRIU/conmon
attach path on rootful container exec; we drop them and lean on the
read-write path scoping + ProtectHome instead, which is sufficient for
this script's actual surface area.

/run is LOAD-BEARING and must be writable as a whole: rootful
`podman exec` -- even a plain exec, no container lifecycle -- grabs
coordination locks across multiple /run subtrees: /run/libpod/
alive.lck (runtime init lock), /run/lock/netavark.lock (network
coordination), /run/containers/ (storage runroot). Listing them
individually is whack-a-mole; each missing one surfaces only at
runtime as "open <path>: read-only file system" (exit 125). /run is
tmpfs runtime state, so granting it RW is low-risk and is exactly
podman's requirement. That exit-125 failure is silent-deadly here:
forge-firstboot.sh's `admin user create` idempotency guard mis-reads
125 as "user already exists", so the admin is never created, the
repo-create 401s, the runner-token mint fails, and the entire
self-replication CI chain (runner-firstboot -> .runner ->
mios-forgejo-runner.service) stays dead behind unmet
ConditionPathExists guards. Operator-confirmed regression 2026-05-14.

<!-- mios-src:525f471875d5 from usr/lib/systemd/system/mios-forge-firstboot.service:32-53 -->

### Runs AFTER the operator's Forgejo admin user and the empty...

Runs AFTER the operator's Forgejo admin user and the empty mios.git
repo are in place (mios-forge-firstboot.service drops the admin
password file and bootstraps the user). The script itself is
idempotent (/.git presence is the sentinel) and short-circuits if
Forgejo or the repo aren't reachable yet -- so re-running on every
boot is harmless.

<!-- mios-src:80bd6c2eb357 from usr/lib/systemd/system/mios-git-root-init.service:6-11 -->

### systemd-udev-settle is deprecated; using udev-trigger...

systemd-udev-settle is deprecated; using udev-trigger instead.
Before=podman.socket docker.socket removed: combining DefaultDependencies=yes
(which implies After=basic.target -> After=sockets.target -> After=podman.socket)
with Before=podman.socket creates an ordering cycle that systemd resolves by
skipping all three GPU services. The service still runs before containers are
used because WantedBy=multi-user.target fires after sockets.target anyway.

<!-- mios-src:2b6b463d5603 from usr/lib/systemd/system/mios-gpu-status.service:9-14 -->

### Default hacluster password is "mios" -- 'MiOS' default...

Default hacluster password is "mios" -- 'MiOS' default, override via bootstrap drop-in:
  /etc/systemd/system/mios-ha-bootstrap.service.d/hacluster.conf
  [Service]
  Environment=[redacted]
  ExecStart=
  ExecStart=/bin/bash -c "echo 'hacluster:${HA_PASSWORD}' | chpasswd && pcs host auth $(hostname -s) -u hacluster -p '${HA_PASSWORD}' && pcs cluster setup --name mios-ha $(hostname -s) --force && pcs cluster start --all && pcs property set stonith-enabled=false"

<!-- mios-src:ef3c6fb82a88 from usr/lib/systemd/system/mios-ha-bootstrap.service:14-19 -->

### usr/lib/systemd/system/mios-hermes-browser-worker.service A...

/usr/lib/systemd/system/mios-hermes-browser-worker.service

A SECOND headless ChromeDev (com.google.ChromeDev flatpak) with CDP on
127.0.0.1:9223 -- the dedicated browser for the Hermes WORKER (:8643).
Distinct from the primary :9222 browser (mios-hermes-browser.service): the CDP
supervisor attaches to the FIRST page target (Target.getTargets ->
next(type=='page')) over a shared browser-level socket, so two workers sharing
one browser would stomp each other's navigation/DOM/cookies. The launcher's
HERMES_BROWSER_CDP_PORT + HERMES_BROWSER_PROFILE_DIR overrides + profile-scoped
kill_existing make a second instance cleanly isolatable.

<!-- mios-src:f52ed5f6cec0 from usr/lib/systemd/system/mios-hermes-browser-worker.service:4-13 -->

### usr/lib/systemd/system/mios-hermes-browser.service Headless...

/usr/lib/systemd/system/mios-hermes-browser.service

Headless ChromeDev (com.google.ChromeDev flatpak) with Chrome
DevTools Protocol on 127.0.0.1:9222 -- the CDP endpoint that
Hermes-Agent's browser tool attaches to (see browser.cdp_url in
/var/lib/mios/hermes/config.yaml). Operator directive 2026-05-15:
"Hermes-Browser isn't enabled!! Should be using the locally
installed ChromeDev flatpak install".

This is the LOCAL backend for browser_navigate / browser_snapshot /
browser_click etc. -- distinct from the cloud-mode backends
(Browserbase, Browser Use) the agent's browser_tool.py also
supports. Cloud mode kicks in automatically if the corresponding
API keys are present in the env; local-via-CDP is the MiOS default
(no off-host calls, no API keys, no cost).

<!-- mios-src:0be669a51b99 from usr/lib/systemd/system/mios-hermes-browser.service:4-18 -->

### ChromeDev flatpak must be installed (system or user scope)....

ChromeDev flatpak must be installed (system or user scope). If
missing, the unit no-ops cleanly instead of crash-looping; operator
fixes by `flatpak install flathub com.google.ChromeDev`.

<!-- mios-src:0f19102d4dda from usr/lib/systemd/system/mios-hermes-browser.service:23-25 -->

### HEADLESS (operator 2026-05-31 "fix the browser/CDP crash")...

HEADLESS (operator 2026-05-31 "fix the browser/CDP crash"): this SERVICE runs
ChromeDev headless so the CDP backend (:9222) comes up RELIABLY. History: the
unit used to set DISPLAY=:0 / WAYLAND_DISPLAY=wayland-0 to open ChromeDev
VISIBLY (operator 2026-05-16), with a comment claiming the launcher would
"auto-fall-back to headless on a host with no display." That assumption was
WRONG -- setting DISPLAY makes the launcher's auto-detect pick VISIBLE mode
(HEADLESS=0), and the WSLg display lives in the operator's INTERACTIVE session,
unreachable from this background (mios-ai, non-interactive) service -> Chrome
could not open a window and crash-looped (ran ~30s then died; CDP never bound,
so browser_navigate always failed "no CDP browser"). Forcing headless sidesteps
the display entirely; CDP is identical. To WATCH the agent browse in a visible
window, run `mios-hermes-browser ensure` from an INTERACTIVE session (real
DISPLAY) -- the launcher auto-detects it and opens headed there.

<!-- mios-src:3ca119b3688f from usr/lib/systemd/system/mios-hermes-browser.service:49-61 -->

### Use the PERSISTENT `start` path, not the default `ensure`...

Use the PERSISTENT `start` path, not the default `ensure`: `ensure`
backgrounds Chrome and EXITS once CDP responds, but Type=simple then treats
that exit as "service stopped" and tears down the cgroup -- killing the very
Chrome it just launched (operator 2026-05-22: "DevTools listening" appeared
then the service went inactive). `start` exec's ChromeDev in the foreground
so it IS the main process and stays up; the agent's pre-flight
`mios-hermes-browser ensure` then just sees CDP already up.

<!-- mios-src:4f99aa5a5d80 from usr/lib/systemd/system/mios-hermes-browser.service:69-75 -->

### Drain timeout

Drain timeout: ChromeDev with CDP has no graceful in-flight protocol
(CDP clients can reconnect transparently). 30 s SIGTERM is plenty to
flush the on-disk profile + cookies. Consolidation 2026-05-15.

<!-- mios-src:7610d0aa84ec from usr/lib/systemd/system/mios-hermes-browser.service:85-87 -->

### ProtectSystem=strict here would block /var/lib/flatpak read...

ProtectSystem=strict here would block /var/lib/flatpak read access;
leave at default. The flatpak sandbox does its own confinement.
PrivateTmp was true, but on WSLg the X11 display socket lives at
/tmp/.X11-unix/X0 -- a private /tmp hides it, so a VISIBLE ChromeDev
launch (DISPLAY=:0) can't reach the display and CDP never binds
(operator 2026-05-22). Off so the WSLg socket is reachable; the flatpak
sandbox still confines the browser itself.

<!-- mios-src:70f5d8eb986a from usr/lib/systemd/system/mios-hermes-browser.service:92-98 -->

### Runs before the DIRECT-install hermes-agent.service so the...

Runs before the DIRECT-install hermes-agent.service so the gateway
starts with a valid $HERMES_HOME/config.yaml + api.env already on
disk. The pre-2026-05-14 ordering targeted mios-hermes.service /
mios-hermes-workspace.service -- both deleted when the Hermes
container Quadlets were removed; hermes-agent.service is the runtime
now.

<!-- mios-src:8dd00094cd2a from usr/lib/systemd/system/mios-hermes-firstboot.service:6-11 -->

### NO ConditionPathExists=!/etc/mios/hermes/api.env. The old...

NO ConditionPathExists=!/etc/mios/hermes/api.env. The old gate made
this unit a true once-ever oneshot -- but the script does TWO jobs:
(1) mint api.env (genuinely once), and (2) seed/heal
/var/lib/mios/hermes/config.yaml (must re-run when the Hermes config
SCHEMA drifts across upgrades, or when the container->direct-install
migration left $HERMES_HOME orphan-owned). The script is fully
idempotent -- it skips keygen when API_SERVER_KEY exists and only
rewrites config.yaml on detected drift -- so letting it run every
boot is cheap and self-healing. Operator-confirmed 2026-05-14: the
gate left a stale pre-0.13 config.yaml in place that the firstboot
rewrite could never reach.

<!-- mios-src:e7183040201e from usr/lib/systemd/system/mios-hermes-firstboot.service:14-24 -->

### Read MIOS_AI_* + model-tier vars from the env bridge so a...

Read MIOS_AI_* + model-tier vars from the env bridge so a fresh systemd
boot has the resolved config (model pick, endpoints). Optional ('-') so
the unit still self-heals if the bridge isn't generated yet.
install-robustness 2026-06-21.

<!-- mios-src:ebed3892724d from usr/lib/systemd/system/mios-hermes-firstboot.service:29-32 -->

### usr/lib/systemd/system/mios-hermes-tail.service Background...

/usr/lib/systemd/system/mios-hermes-tail.service

Background tail of hermes-agent.service journal -- extracts in-flight
delegate_task + tool-call + retry + invalid-tool-call events, writes
/var/lib/mios/hermes-tail/latest.json which the OWUI mios_sidecar
Filter polls. This is the bridge that surfaces "what Hermes is
doing right now" into the OWUI chat stream via __event_emitter__,
per operator directive 2026-05-16 ("MiOS-Agent prints using OWUIs
emitters functions the current global statuses from
Hermes-Agent(s)/Sub-Agents").

Runs as root so it has unrestricted journal read access; writes a
world-readable state file so mios-open-webui (uid 817, no
systemd-journal group) can poll without permission gymnastics.

<!-- mios-src:3878d1a8bfc3 from usr/lib/systemd/system/mios-hermes-tail.service:4-17 -->

### Enforce go+rX on /usr/libexec/mios so services running as a...

Enforce go+rX on /usr/libexec/mios so services running as a DIFFERENT user
than the file owner can exec their scripts.

Why this exists: the deployed root / is a git working tree of mios.git. A
checkout without core.fileMode (or a committed source lacking the exec bit)
strips group/other execute from the libexec scripts, leaving them mode 0744
owned by the operator user. Any service that ExecStart's such a script as a
non-owner service user (mios-ai / mios-skills / mios-hermes / ...) then fails
EXEC with 203/"Permission denied" and crash-loops (observed: hermes-browser at
12k+ restarts, skills-miner failed, daemon/mcp inert). `chmod -R go+rX` is
idempotent and, via the capital X, only propagates EXISTING owner-execute --
it never makes a data file executable.

<!-- mios-src:c6f47c928718 from usr/lib/systemd/system/mios-libexec-perms.service:4-15 -->

### Trigger group (ORed): run only when a MOK cert is actually...

Trigger group (ORed): run only when a MOK cert is actually baked into the
image -- the operator-generated key (generate-mok-key.sh) OR, on ucore-hci
variants, the pre-signed ublue akmods key. Same two paths enroll-mok.sh's
pick_key() resolves. No key baked -> unit is a clean no-op.

<!-- mios-src:0d0304c7f565 from usr/lib/systemd/system/mios-mok-enroll.service:9-12 -->

### Makes [agents.opencode] endpoint :${MIOS_PORTS_OPENCODE_GATEWAY}/v1 a REAL OpenAI...

Makes [agents.opencode] endpoint :${MIOS_PORTS_OPENCODE_GATEWAY}/v1 a REAL OpenAI endpoint so
agent-pipe's multi-agent fan-out (opencode secondary) + the primary path
(refine target_agent=opencode) can reach it. opencode has no native /v1
(its `serve` is opencode's own OpenAPI on :4096); this shim wraps
`opencode run`. FOSS + offline (opencode -> local mios-llm-light coder model).

FRONT-DOOR commitment (operator 2026-05-31): opencode is a first-class /v1
council peer via this gateway (NOT a Hermes ACP subprocess). The shim now
passes the FULL conversation (system + history) to `opencode run`, lands its
config at MIOS_OPENCODE_CONFIG, unifies the model id, and streams proper SSE
deltas. Deployed + enabled by the UNIFIED agent-plane install driver
automation/72-hermes-agent.sh: PHASE 1 builds the shared venv + Hermes,
PHASE 2 fetches the opencode binary + lands opencode.json. The gateway
server.py itself ships via the system-files overlay (this file's tree).

This unit lives in /usr/lib/systemd/system, which 34-render-quadlets.sh does
NOT process, and systemd does not expand env vars in User=/WorkingDirectory=/
ConditionPathExists=/ExecStart=. So the values below are LITERALS that MIRROR
mios.toml ([ports].opencode_gateway, [ai].opencode_*, [ai].agent_venv) — the
same convention hermes-agent.service uses. If a value changes in mios.toml,
update this unit to match.

Skip cleanly if the opencode binary never landed (build-time fetch failure)
rather than crash-loop; `mios update` re-runs 72-hermes-agent.sh (PHASE 2)
to complete it.

<!-- mios-src:4e02b33863bc from usr/lib/systemd/system/mios-opencode-gateway.service:10-34 -->

### Tighten ownership for the agents we know map to sysusers....

Tighten ownership for the agents we know map to sysusers. The
CLI itself runs as root (so it can chown after writing); these
post-steps narrow the private-key permission to the agent's
sysuser. We do NOT fail the unit if an agent's sysuser doesn't
exist yet (newer agents enrolled before their service ships) --
the `-` prefix swallows errors.
agent-pipe was CUT OVER to run as mios-ai (user consolidation), so its
passport private.key (0600) must be owned by mios-ai or the service can't read
it. Chowning to the OLD mios-agent-pipe left it unreadable by the mios-ai
service (operator-confirmed 2026-05-31: "passport: failed to load private key:
Permission denied" -> agent-pipe couldn't sign its agent-DB writes). hermes /
mios-daemon stay on their own sysusers below until their services are likewise
cut over to mios-ai.

<!-- mios-src:097819a761c0 from usr/lib/systemd/system/mios-passport-provision.service:28-40 -->

### Public keys

Public keys: 0640 group=mios-ai (group-read only by the AI bucket
group). USER/SYSTEM/AI separation -- the operator (`mios`) is in
mios-ai so still reads; non-AI sysusers no longer get world-read.

<!-- mios-src:352d77c9a177 from usr/lib/systemd/system/mios-passport-provision.service:46-48 -->

### SSOT env

SSOT env: MIOS_PGVECTOR_USER / MIOS_PGVECTOR_DB / MIOS_PORTS_PGVECTOR / MIOS_PG_BACKUP_*
all flow from mios.toml [pgvector] -> userenv.sh. '-' = tolerate absence
(degrade-open to the inline defaults below).

<!-- mios-src:ecafee76085a from usr/lib/systemd/system/mios-pgvector-backup.service:32-34 -->

### Runs as root to `podman exec` into the mios-ai.pod pgvector...

Runs as root to `podman exec` into the mios-ai.pod pgvector container -- the SAME
proven pattern mios-sys-env-refresh uses. The pg listens loopback-only INSIDE the
pod (no PublishPort), so the old unprivileged host-side pg_dump could never reach
it (silent degrade). podman needs its runtime writable, so ProtectSystem=strict /
ReadWritePaths are dropped; keep the light hardening below.

<!-- mios-src:3c0dd2e02eb9 from usr/lib/systemd/system/mios-pgvector-backup.service:46-50 -->

### Logical dump over loopback-trust, gzip'd + timestamped...

Logical dump over loopback-trust, gzip'd + timestamped, then prune to the
newest N. Pure POSIX sh so it runs on the minimal base. Every branch exits 0
(degrade-open): gate-off, missing client, or a dump error logs and succeeds.

<!-- mios-src:49add09871d2 from usr/lib/systemd/system/mios-pgvector-backup.service:53-55 -->

### Containers run ROOTFUL; any NON-root context -- the...

Containers run ROOTFUL; any NON-root context -- the hardened agent-pipe
(User=mios-agent-pipe), the launcher broker, AND an operator's interactive
SSH/Termius shell -- running `podman ps` sees NOTHING (/run/podman is 0700
root:root). This root oneshot writes a READ-ONLY `podman ps` snapshot that
every non-root reader (portal, container_status verb, operator shell)
consumes instead. Operator 2026-05-23: containers invisible in the dashboard
AND in the Termius iPhone app -- same root cause.

WORLD-READABLE shared path: /var/lib/mios is root:root 755 (traversable by
all), and the file is chmod 0644 -- so the operator's SSH user can read it,
not just mios-agent-pipe (whose state dir is 0750 and blocked everyone else).

<!-- mios-src:485b886a1cf8 from usr/lib/systemd/system/mios-podman-ps.service:11-21 -->

### Best-effort

Best-effort: a missing python3/tomllib must never block boot. Consumers
(Theme.qml, mios-app-shell.css include sites) all degrade-open to the
vendor-default palette when /etc/mios/theme/* is absent.

<!-- mios-src:6acf13dd0069 from usr/lib/systemd/system/mios-sync-theme.service:20-22 -->

### R6: bound the Restart=on-failure below so a cold-DB retry...

R6: bound the Restart=on-failure below so a cold-DB retry storm gives up cleanly
after 5 tries / 300s instead of hammering (and never landing permanently failed
inside the default 5/10s window).

<!-- mios-src:1b6fe3efdaf9 from usr/lib/systemd/system/mios-sys-env-refresh.service:11-13 -->

### Probe the live environment (launchable apps + stack...

Probe the live environment (launchable apps + stack services + loaded models +
host HW) and UPSERT the shared `sys_env:current` row so EVERY agent reads a
current snapshot from the DB -- the env analogue of mios-podman-ps (dashboard)
and the daemon's directory_entry cache. Read-only probe; the only write is the
pgvector cache row. operator 2026-05-23: "probe systems/environment live +
store/update a mios-sys-env database".
systemd does NOT expand bash ${VAR:-default} in Environment= -- it passes the
LITERAL, unexpanded ${VAR:-default} string, which mios-pg-query then int()s ->
ValueError -> "pgvector upsert failed" on every run. Source install.env instead
so MIOS_PORTS_PGVECTOR holds the real port; mios-pg-query reads it directly
(its own fallback is MIOS_PG_PORT or MIOS_PORTS_PGVECTOR or 5432).

<!-- mios-src:b83a92415520 from usr/lib/systemd/system/mios-sys-env-refresh.service:19-29 -->

### R6: real readiness gate -- block ExecStart until pgvector...

R6: real readiness gate -- block ExecStart until pgvector actually answers so a
cold DB no longer produces a permanent 'failed' unit. NO `|| true`: this must
FAIL (and drive the bounded Restart below) until PG is up. ${MIOS_PORTS_PGVECTOR}
expands from the EnvironmentFile above; /usr/bin/pg_isready ships with the
postgresql client (absolute path -- PATH is not guaranteed in the unit context).

<!-- mios-src:82f1edf23fdd from usr/lib/systemd/system/mios-sys-env-refresh.service:31-35 -->

### Run as the operator login user so the session inherits the...

Run as the operator login user so the session inherits the
operator's $HOME, environment, and shell rc files. Connecting
from a browser gives the SAME shell experience as the operator's
WSL terminal.

<!-- mios-src:6d9300946c47 from usr/lib/systemd/system/mios-ttyd-bash.service:26-29 -->

### Run as the operator -- WSL interop's /init creates per-user...

Run as the operator -- WSL interop's /init creates per-user
Windows interop sockets, so the agent service-user contexts
CAN'T exec /mnt/c/Windows/System32/powershell.exe (operator-
confirmed 2026-05-15 in mios-as-operator.sh's perm wall note).
The browser session is the operator's anyway -- single tenant.

<!-- mios-src:9f38ed496740 from usr/lib/systemd/system/mios-ttyd-powershell.service:36-40 -->

### WINE ships /usr/lib/binfmt.d/wine.conf which registers...

WINE ships /usr/lib/binfmt.d/wine.conf which registers binfmt_misc handlers
`windows` + `windowsPE` (interpreter /usr/bin/wine) that match ANY PE/.exe by
magic -- including /mnt/c/Windows/*.exe. When those win over WSL's `WSLInterop`
(interpreter /init), running a real Windows exe (powershell.exe, cmd.exe,
msrdc, the OS-control executor) launches it under WINE instead of on the real
Windows host -- so MiOS-Agent OS-control (window-list, launch verify, the
Windows executor) goes BLIND (count:0, WINE fixme noise in JSON). The WSL
2.8.x upgrade re-ordered binfmt so WINE won. Operator-hit 2026-06-06.

Fix: after systemd-binfmt registers everything, DISABLE the WINE handlers so
WSLInterop handles .exe. WINE apps still run via explicit `wine app.exe` (and
their .desktop launchers, which call wine) -- only bare ./app.exe auto-run via
binfmt is dropped, which is the right trade for a WSL distro whose primary job
is real-Windows interop.

<!-- mios-src:735884b1b343 from usr/lib/systemd/system/mios-wsl-interop-priority.service:6-19 -->

### 'MiOS' WSL2 user-runtime-directory fallback. pam_systemd...

'MiOS' WSL2 user-runtime-directory fallback.
pam_systemd creates /run/user/<uid> on a real PAM login session. WSL2's
default-user invocation does open a PAM session, but `wsl -u root` followed
by `su - mios` bypasses pam_systemd and leaves /run/user/1000 missing,
which then surfaces as "Unsupported or missing session type ''" in GTK
apps, dconf "Permission denied", and dbus user-session failures.

This service unconditionally creates the directory for the canonical mios
user on WSL2 boots so the cascade can't happen. On a real logind session
the directory already exists -- install -d is idempotent and a no-op.

<!-- mios-src:16e0a3f4b96c from usr/lib/systemd/system/mios-wsl-runtime-dir.service:4-13 -->

### WSL2 mounts /mnt/wslg/runtime-dir with mode 0777...

WSL2 mounts /mnt/wslg/runtime-dir with mode 0777 (world-writable),
but weston's wayland-server treats anything looser than 0700 as a
protocol violation and refuses to advance past the RAIL fallback
into VAIL (shared-memory) mode. Operator-confirmed via weston.log:
  warning: XDG_RUNTIME_DIR "/mnt/wslg/runtime-dir" is not configured
  correctly.  Unix access mode must be 0700 (current mode is 777)

This service runs ONCE at boot, BEFORE weston starts, chmoding the
directory to 0700. It's a precondition for VAIL; combined with WSL
2.7.3+'s shared-memory handshake (provided wslservice on the
Windows host successfully exposes the Section object), this should
nudge weston past COPY MODE into native VAIL rendering.

Note: this is a NECESSARY but not SUFFICIENT condition. Some
COPY MODE deployments persist due to host-side wslservice problems
that no Linux-side fix can address (per WSLg upstream issues #312,
#972, #982, #1278). When that's the case the warning disappears
but RAIL fallback stays -- diagnose via `WAYLAND_DEBUG=client
weston-info` on the host side.

<!-- mios-src:2f640cad5b85 from usr/lib/systemd/system/mios-wslg-permissions-fix.service:8-26 -->

### tmp/.X11-unix

/tmp/.X11-unix: WSL2 ships it as a SYMLINK -> /mnt/wslg/.X11-unix.
Flatpak bwrap chokes on symlinks ("Can't mount tmpfs on
/newroot/tmp/.X11-unix: No such file or directory") for any GUI app
that uses --socket=fallback-x11 (Nautilus.Devel, gnome-text-editor,
epiphany under XWayland fallback, etc.). Replace symlink with a
real directory + bind-mount the host's WSLg X11 socket dir onto
it so bwrap can chroot freely without losing X11 access.
Operator-flagged 2026-05-10: "bwrap: Can't mount tmpfs on
/newroot/tmp/.X11-unix: No such file or directory" when launching
Nautilus.Devel from gnome-nightly.

<!-- mios-src:cdfae792d28a from usr/lib/systemd/system/mios-wslg-permissions-fix.service:38-47 -->

### Sanitize malformed flatpak-exported icons (a ".svg" that is...

Sanitize malformed flatpak-exported icons (a ".svg" that is really a PNG)
BEFORE weston builds its RAIL app-list. rsvg returns NULL on a non-SVG
and weston NULL-derefs -> SIGSEGV-loops until WSLGd gives up, which makes
the wayland/X11 sockets refuse ALL clients (every GUI launch invisible).
Operator-hit 2026-06-06: com.google.ChromeDev shipped a PNG named
com.google.ChromeDev.svg on WSL 2.7.7.0. See the helper's header.

<!-- mios-src:8577afbd08c5 from usr/lib/systemd/system/mios-wslg-permissions-fix.service:54-59 -->
### usr/lib/systemd/system/hermes-worker.service The MiOS...

/usr/lib/systemd/system/hermes-worker.service

The MiOS Hermes gateway. It is the DISPATCH target of [agents.hermes].endpoint
and does its own heavy-lane inference, so it never relays back to agent-pipe.
  * API_SERVER_PORT is the LOAD-BEARING bind var; PORT is inert in this Hermes
    build (no PORT->API_SERVER_PORT bridge). Both carry the `hermes` port key.
  * SEPARATE HERMES_HOME=/var/lib/mios/hermes-worker => its own gateway.pid /
    gateway.lock / gateway_state.json / state.db / kanban.db / config.yaml.
  * NO discord.env / NO DISCORD_BOT_TOKEN => the Discord adapter never calls
    _acquire_platform_lock('discord-bot-token', ...), so no host-global lock
    is contended and no SIGTERM flap can follow.
  * NO --replace: the HERMES_HOME-scoped pidfile is its own, and the eviction
    scan is profile-scoped (only --all crosses profiles, which is not used).

<!-- mios-src:7e38508e9639 from usr/lib/systemd/system/hermes-worker.service:4-16 -->
### SSOT chain

SSOT chain: every operator-tunable knob sources from mios.toml via
/etc/profile.d/mios-env.sh, which exports MIOS_* into the service
environment. The service code reads MIOS_PORTS_AGENT_PIPE +
MIOS_AGENT_PIPE_BACKEND + MIOS_DB_URL etc. as os.environ overrides.

<!-- mios-src:b10e4274c620 from usr/lib/systemd/system/mios-agent-pipe.service:36-39 -->

### SSOT env

SSOT env: MIOS_PGVECTOR_USER / MIOS_PGVECTOR_DB / MIOS_PORTS_PGVECTOR / MIOS_PG_BACKUP_*
all flow from mios.toml [pgvector] -> userenv.sh. '-' = tolerate absence
(degrade-open to the inline defaults below).

<!-- mios-src:ee5005149fcb from usr/lib/systemd/system/mios-pgvector-backup.service:15-17 -->

### R6: real readiness gate -- block ExecStart until pgvector...

R6: real readiness gate -- block ExecStart until pgvector actually answers so a
cold DB no longer produces a permanent 'failed' unit. NO `|| true`: this must
FAIL (and drive the bounded Restart below) until PG is up. ${MIOS_PORTS_PGVECTOR}
expands from the EnvironmentFile above; /usr/bin/pg_isready ships with the
postgresql client (absolute path -- PATH is not guaranteed in the unit context).

<!-- mios-src:89b2d8d93c05 from usr/lib/systemd/system/mios-sys-env-refresh.service:31-35 -->
### Probe the live environment (launchable apps + stack...

Probe the live environment (launchable apps + stack services + loaded models +
host HW) and UPSERT the shared `sys_env:current` row so EVERY agent reads a
current snapshot from the DB -- the env analogue of mios-podman-ps (dashboard)
and the daemon's directory_entry cache. Read-only probe; the only write is the
pgvector cache row. operator 2026-05-23: "probe systems/environment live +
store/update a mios-sys-env database".
systemd does NOT expand bash ${VAR:-default} in Environment= -- it passes the
LITERAL "${VAR:-default}" text, which mios-pg-query then int()s ->
ValueError -> "pgvector upsert failed" on every run. Source install.env instead
so MIOS_PORTS_PGVECTOR holds the real port; mios-pg-query reads it directly
(its own fallback is MIOS_PG_PORT or MIOS_PORTS_PGVECTOR or 5432).

<!-- mios-src:f9af4b2f62dc from usr/lib/systemd/system/mios-sys-env-refresh.service:19-29 -->
