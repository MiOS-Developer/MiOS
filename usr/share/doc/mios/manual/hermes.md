<!-- AI-hint: Manual pages distilled from the source comments of hermes, sanitized, each passage anchored to the comment it came from. -->

# hermes

### DEPRECATED

DEPRECATED: This config has been migrated to the [gateway] section of mios.toml
/usr/share/mios/hermes/config-worker.yaml -- MiOS-seeded NON-THIN Hermes WORKER
(Hermes-Agent 0.13.x schema). Copied to /var/lib/mios/hermes-worker/config.yaml
(the worker's HERMES_HOME) by hermes-worker-firstboot; that path is NEVER touched
by mios-hermes-firstboot (which only re-thins /var/lib/mios/hermes/config.yaml),
so this worker config is durable across boots.

This is the P1 worker: a REAL agent that runs its OWN
native browser_*/CDP + terminal + file + skills tool loop, doing its OWN
inference on the heavy lane (mios-heavy, port key `vllm`, vLLM, --tool-call-parser
qwen25 = native OpenAI tool_calls). It serves the OpenAI /v1 surface on :8643
and is the WORKER-DISPATCH target of [agents.hermes].endpoint in mios.toml.

The gateway on the `hermes` port (hermes-agent.service) is UNAFFECTED -- it stays the thin
Discord/CLI gateway. This worker enables ONLY the api_server platform (NO
Discord token => no contention for the host-global discord-bot-token scope
lock held by the gateway on the `hermes` port).

LOOP-SAFETY: this worker hits the REAL `vllm` model lane for inference, so it
never relays back to the `agent_pipe` port. mcp_servers.mios stays DISABLED here (the relay's
MIOS_AGENT_PIPE_URL pointed at the `agent_pipe` port would let a worker re-enter the orchestrator ->
agent-pipe -> council -> worker cycle). The worker's native toolsets already give
it terminal/file/web/browser/skills without the MCP relay. The P0 hop-budget/
Via guard (_HOP_HEADER/_VIA_HEADER, server.py) is the backstop either way.

<!-- mios-src:11f25a8c9659 from usr/share/mios/hermes/config-worker.yaml:1-24 -->

### api_server bind / port / key / cors come from the...

api_server bind / port / key / cors come from the EnvironmentFile=
/etc/mios/hermes/api.env in the Quadlet (API_SERVER_HOST / _PORT /
_KEY / _CORS_ORIGINS). DO NOT add an `api_server:` block here with
`${API_SERVER_KEY}` placeholders -- the Hermes YAML loader does NOT
expand env vars, so the literal string "${API_SERVER_KEY}" becomes
the accepted bearer token and every real request 401s.
Operator-flagged "Invalid API key" on every /v1 request
after the firstboot config rewrite; root cause was the placeholder
block left behind.

<!-- mios-src:145f79bbc593 from usr/share/mios/hermes/config.yaml:34-42 -->

### Tool surface

Tool surface: hermes is a host service and reaches SearXNG via its
SEARXNG_URL (host/localhost:<port>), so the agent's web_search tool
can call it directly. Architectural Law 5 unaffected -- search is not
AI inference.

<!-- mios-src:ce5da04a58ec from usr/share/mios/hermes/config.yaml:44-47 -->

### Architectural Law 5 (UNIFIED-AI-REDIRECTS) -- the agent...

Architectural Law 5 (UNIFIED-AI-REDIRECTS) -- the agent loop must
stay local. Without this block Hermes routes /v1/chat/completions
to openrouter.ai with anthropic/claude-opus-4.6, which 401s on a
fresh install (no OPENROUTER_API_KEY). custom_providers declares
the local Ollama OpenAI surface; `agent` pins the thinking loop
to that provider. config.local.yaml from build-mios.ps1 re-asserts
these on the dev VM with the Network=host loopback URLs.

<!-- mios-src:963bc9cfe6a9 from usr/share/mios/hermes/config.yaml:54-60 -->

### Phase C.2 of the AgentOS roadmap

Phase C.2 of the AgentOS roadmap: cross-agent skill catalog.
Hermes' OpenAI tool surface auto-extends with every promoted MiOS
skill exposed by the agent-pipe /skills/openai-tools endpoint or
the static /var/lib/mios/skills/catalog.json file (the same JSON
shape; the static file is the offline fallback). Operator points
Hermes at one OR both -- the static path wins on a fresh boot
when the agent-pipe HTTP service hasn't come up yet; HTTP refresh
overrides on later reloads to pick up newly-promoted skills.

<!-- mios-src:c5b56851d200 from usr/share/mios/hermes/config.yaml:103-110 -->

### MCP CLIENT ("Hermes should have access to all Global MiOS...

MCP CLIENT ("Hermes should have access to all Global MiOS
MCP surfaces"). Hermes CONSUMES the global mios-mcp-server, so its tool registry
auto-extends with the COMPLETE MiOS surface -- all 82 verbs + 18 recipes +
promoted skills (tools/list) + the full read-only catalog (resources) -- every
tool_call routed through the launcher broker. stdio = the MCP-canonical transport
(the server's primary mode); the spawned relay forwards to the agent-pipe. This is
the SAME unified /v1/tools surface the pipeline workers already carry
(WORKER_TOOLS_SCOPE=all), so Hermes and the swarm now share one global tool plane.

<!-- mios-src:88ead109b971 from usr/share/mios/hermes/config.yaml:120-127 -->

### Operator overrides

Operator overrides: drop a /etc/mios/hermes/config.local.yaml file
alongside this one to override any of the above. The local file is
merged on top, so partial overrides (e.g. just `backend.model`) work.

<!-- mios-src:e486948c548d from usr/share/mios/hermes/config.yaml:138-140 -->
### DEPRECATED

DEPRECATED: This config has been migrated to the [gateway] section of mios.toml
/usr/share/mios/hermes/config-worker.yaml -- MiOS-seeded NON-THIN Hermes WORKER
(Hermes-Agent 0.13.x schema). Copied to /var/lib/mios/hermes-worker/config.yaml
(the worker's HERMES_HOME) by hermes-worker-firstboot; that path is NEVER touched
by mios-hermes-firstboot (which only re-thins /var/lib/mios/hermes/config.yaml),
so this worker config is durable across boots.

This is the P1 worker: a REAL agent that runs its OWN
native browser_*/CDP + terminal + file + skills tool loop, doing its OWN
inference on the heavy lane (heavy inference port, SGLang, --tool-call-parser
qwen25 = native OpenAI tool_calls). It serves the OpenAI /v1 surface on :8643
and is the WORKER-DISPATCH target of [agents.hermes].endpoint in mios.toml.

The agent gateway port (hermes-agent.service) is UNAFFECTED -- it stays the thin
Discord/CLI gateway. This worker enables ONLY the api_server platform (NO
Discord token => no contention for the host-global discord-bot-token scope
lock held by the agent gateway port).

LOOP-SAFETY: this worker hits the heavy model lane for inference, so it
never relays back to :8700. mcp_servers.mios stays DISABLED here (the relay's
MIOS_AGENT_PIPE_URL=:8700 would let a worker re-enter the orchestrator ->
:8700 -> council -> worker cycle). The worker's native toolsets already give
it terminal/file/web/browser/skills without the MCP relay. The P0 hop-budget/
Via guard (_HOP_HEADER/_VIA_HEADER, server.py) is the backstop either way.

<!-- mios-src:28f89470ae3b from usr/share/mios/hermes/config-worker.yaml:1-24 -->
### DEPRECATED

DEPRECATED: This config has been migrated to the [gateway] section of mios.toml
/usr/share/mios/hermes/config-worker.yaml -- MiOS-seeded NON-THIN Hermes WORKER
(Hermes-Agent 0.13.x schema). Copied to /var/lib/mios/hermes-worker/config.yaml
(the worker's HERMES_HOME) by hermes-worker-firstboot; that path is NEVER touched
by mios-hermes-firstboot (which only re-thins /var/lib/mios/hermes/config.yaml),
so this worker config is durable across boots.

This is the P1 worker: a REAL agent that runs its OWN
native browser_*/CDP + terminal + file + skills tool loop, doing its OWN
inference on the heavy lane ([ports].llm_heavy, mios-heavy; its engine, vLLM or SGLang, is
[ai].heavy_engine and emits native OpenAI tool_calls). It serves the OpenAI /v1 surface on :8643
and is the WORKER-DISPATCH target of [agents.hermes].endpoint in mios.toml.

The [ports].hermes gateway (hermes-agent.service) is UNAFFECTED -- it stays the thin
Discord/CLI gateway. This worker enables ONLY the api_server platform (NO
Discord token => no contention for the host-global discord-bot-token scope
lock held by the [ports].hermes gateway).

LOOP-SAFETY: this worker hits the REAL [ports].llm_heavy model lane for inference, so it
never relays back to :8700. mcp_servers.mios stays DISABLED here (the relay's
MIOS_AGENT_PIPE_URL=:8700 would let a worker re-enter the orchestrator ->
:8700 -> council -> worker cycle). The worker's native toolsets already give
it terminal/file/web/browser/skills without the MCP relay. The P0 hop-budget/
Via guard (_HOP_HEADER/_VIA_HEADER, server.py) is the backstop either way.

<!-- mios-src:d9d0cbd7e601 from usr/share/mios/hermes/config-worker.yaml:4-27 -->

### Degrade to the light lane (granite4.1:8b on...

Degrade to the light lane (granite4.1:8b on [ports].llm_light) if the heavy lane OOMs
(the heavy engine OOMs at gaming-time per memory). Walks DOWN the tier, never up.

<!-- mios-src:56f33b0fd375 from usr/share/mios/hermes/config-worker.yaml:42-43 -->
