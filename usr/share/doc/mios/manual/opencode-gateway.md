<!-- AI-hint: Manual pages distilled from the source comments of opencode-gateway, sanitized, each passage anchored to the comment it came from. -->

# opencode-gateway

### MiOS opencode → OpenAI /v1 gateway shim. opencode (the...

MiOS opencode → OpenAI /v1 gateway shim.

opencode (the SST/charm CLI coding agent) speaks its own CLI protocol, not the
OpenAI /v1 chat-completions contract that the MiOS agent-pipe council expects.
This shim wraps `opencode run` behind a minimal OpenAI-compatible HTTP server so
opencode can be dispatched as a first-class /v1 council peer (like Hermes on
the `hermes` port), without teaching agent-pipe a bespoke protocol.

Endpoints:
  GET  /v1/models            → advertise the single opencode model id
  POST /v1/chat/completions  → run opencode, return an OpenAI chat.completion
                               (or an SSE delta stream when stream=true)

Config (all via env, SSOT-rendered by the unit / userenv.sh):
  MIOS_PORTS_OPENCODE_GATEWAY   listen port (SSOT key [ports].opencode_gateway)
  MIOS_OPENCODE_BIN            path to the opencode binary
  MIOS_OPENCODE_MODEL          model id to advertise/forward (ONE canonical id;
                               must match [agents.opencode].model + the key in
                               opencode.json)
  MIOS_OPENCODE_PROVIDER       opencode provider name from opencode.json
                               (default "local"); used to build the `-m
                               provider/model` selector
  MIOS_OPENCODE_CONFIG         explicit path to opencode.json; exported to the
                               child as OPENCODE_CONFIG so opencode does NOT
                               depend on a hardcoded /root/.config location
  MIOS_OPENCODE_HOST           bind host (default 127.0.0.1)
  MIOS_OPENCODE_TIMEOUT_S      per-run timeout seconds (default 90; SSOT key
                               [ai].opencode_gateway_timeout_s). Legacy
                               MIOS_OPENCODE_TIMEOUT is still honoured as a
                               fallback for older overlays.

<!-- mios-src:2a299fcfb3e3 from usr/lib/mios/agents/opencode-gateway/server.py:5-36 -->

### Collapse an OpenAI messages array into a single opencode...

Collapse an OpenAI messages array into a single opencode `run` prompt.

    opencode's non-interactive `run` takes one prompt string, so we serialise
    the whole conversation (system + history + latest user turn) into a clearly
    delimited transcript instead of dropping everything but the last user line.
    Returns (system_prompt, prompt_text).

<!-- mios-src:93ef4e2f3aa3 from usr/lib/mios/agents/opencode-gateway/server.py:77-83 -->

### Invoke `opencode run` with the unified config + model...

Invoke `opencode run` with the unified config + model selector.

    Returns the assistant text. Raises on failure.

    interactive TUI (spinner/replay frames interleaved with the answer) which
    (a) pollutes the returned text with control noise and (b) wedges when stdout
    is a partial / early-closed consumer -- the long-standing "opencode run
    hangs / returns zero" symptom that made this peer inert (
    opencode default=false/fanout=false). `--format json` emits ONE JSON event
    per line (step_start / text / step_finish) with no TUI, so the process exits
    promptly at step_finish and we extract exactly the assistant text. Verified
    on opencode 1.17.7: a default-format run wedged behind a truncating pipe;
    --format json completed cleanly (step_finish reason=stop) every time.

<!-- mios-src:fd05adc8a631 from usr/lib/mios/agents/opencode-gateway/server.py:123-136 -->

### Emit a well-formed OpenAI SSE delta stream. opencode's...

Emit a well-formed OpenAI SSE delta stream.

        opencode's `run` is not token-incremental over a stable public API, so
        we run it to completion then chunk the result into SSE deltas. This
        keeps stream=true callers (agent-pipe council) happy with a valid
        chat.completion.chunk stream terminated by [DONE]. `model` is the SURFACE
        id echoed in every chunk ("MiOS AI"); `run_model` is the internal opencode
        subprocess selector (defaults to `model` for back-compat callers).

<!-- mios-src:3242f7b0c8f2 from usr/lib/mios/agents/opencode-gateway/server.py:269-277 -->
### MiOS opencode → OpenAI /v1 gateway shim. opencode (the...

MiOS opencode → OpenAI /v1 gateway shim.

opencode (the SST/charm CLI coding agent) speaks its own CLI protocol, not the
OpenAI /v1 chat-completions contract that the MiOS agent-pipe council expects.
This shim wraps `opencode run` behind a minimal OpenAI-compatible HTTP server so
opencode can be dispatched as a first-class /v1 council peer (like Hermes at
:8720), without teaching agent-pipe a bespoke protocol.

Endpoints:
  GET  /v1/models            → advertise the single opencode model id
  POST /v1/chat/completions  → run opencode, return an OpenAI chat.completion
                               (or an SSE delta stream when stream=true)

Config (all via env, SSOT-rendered by the unit / userenv.sh):
  MIOS_PORTS_OPENCODE_GATEWAY   listen port (default 8780)
  MIOS_OPENCODE_BIN            path to the opencode binary
  MIOS_OPENCODE_MODEL          model id to advertise/forward (ONE canonical id;
                               must match [agents.opencode].model + the key in
                               opencode.json)
  MIOS_OPENCODE_PROVIDER       opencode provider name from opencode.json
                               (default "local"); used to build the `-m
                               provider/model` selector
  MIOS_OPENCODE_CONFIG         explicit path to opencode.json; exported to the
                               child as OPENCODE_CONFIG so opencode does NOT
                               depend on a hardcoded /root/.config location
  MIOS_OPENCODE_HOST           bind host (default 127.0.0.1)
  MIOS_OPENCODE_TIMEOUT_S      per-run timeout seconds (default 90; SSOT key
                               [ai].opencode_gateway_timeout_s). Legacy
                               MIOS_OPENCODE_TIMEOUT is still honoured as a
                               fallback for older overlays.

<!-- mios-src:89df15f298de from usr/lib/mios/agents/opencode-gateway/server.py:4-35 -->
### MiOS opencode → OpenAI /v1 gateway shim. opencode (the...

MiOS opencode → OpenAI /v1 gateway shim.

opencode (the SST/charm CLI coding agent) speaks its own CLI protocol, not the
OpenAI /v1 chat-completions contract that the MiOS agent-pipe council expects.
This shim wraps `opencode run` behind a minimal OpenAI-compatible HTTP server so
opencode can be dispatched as a first-class /v1 council peer (like Hermes at
:8720), without teaching agent-pipe a bespoke protocol.

Endpoints:
  GET  /v1/models            → advertise the single opencode model id
  POST /v1/chat/completions  → run opencode, return an OpenAI chat.completion
                               (or an SSE delta stream when stream=true)

Config (all via env, SSOT-rendered by the unit / userenv.sh):
  MIOS_PORTS_OPENCODE_GATEWAY   listen port (default 8780)
  MIOS_OPENCODE_BIN            path to the opencode binary
  MIOS_OPENCODE_MODEL          model id to advertise/forward (ONE canonical id;
                               must match [agents.opencode].model + the key in
                               opencode.json)
  MIOS_OPENCODE_PROVIDER       opencode provider name from opencode.json
                               (default "local"); used to build the `-m
                               provider/model` selector
  MIOS_OPENCODE_CONFIG         explicit path to opencode.json; exported to the
                               child as OPENCODE_CONFIG so opencode does NOT
                               depend on a hardcoded /root/.config location
  MIOS_OPENCODE_HOST           bind host (default 127.0.0.1)
  MIOS_OPENCODE_TIMEOUT_S      per-run timeout seconds (default 90; SSOT key
                               [ai].opencode_gateway_timeout_s). Legacy
                               MIOS_OPENCODE_TIMEOUT is still honoured as a
                               fallback for older overlays.

<!-- mios-src:99ef421a4520 from usr/lib/mios/agents/opencode-gateway/server.py:4-35 -->
