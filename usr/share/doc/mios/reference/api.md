<!-- AI-hint: Defines the OpenAI-compatible API surface that the unified MiOS AI endpoint (MIOS_AI_ENDPOINT, Architectural Law 5) targets, and maps it onto the MiOS inference lanes (mios-llm-light llama.cpp primary, mios-llm-heavy vLLM gated; [ports] keys llm_light/vllm) fronted by the agent-pipe orchestrator and MiOS-Hermes gateway ([ports] keys agent_pipe/hermes). Primary reference for any agent or OpenAI-API client talking to MiOS.
     AI-related: /usr/lib/mios/logs/openai-api-verification.txt, /usr/share/mios/ai/system.md, /usr/share/mios/ai/INDEX.md, /usr/share/mios/llamacpp/mios-llm-light.yaml, /usr/share/mios/postgres/schema-init.sql, /usr/lib/mios/agent-pipe/server.py, mios-llm-light, mios-llm-heavy, mios-pgvector, mios-bootstrap, mios-dev, mios-build-local, mios-ceph, mios-k3s -->
# usr/share/doc/mios/reference/api.md

> Canonical OpenAI-API-compatible reference for MiOS.
>
> **What MiOS is, in one line.** 'MiOS' is an immutable, `bootc`-managed
> Fedora-derived workstation shipped as a single OCI image that is *also* a
> local, self-hosted, OpenAI-compatible agentic AI operating system. Everything
> on the box -- CLI tools, the `mios` agent, IDE plugins, the desktop UI -- talks
> to one brain through one endpoint. This document specifies that endpoint's
> wire protocol.
>
> **Scope.** This document specifies the OpenAI public API surface that 'MiOS'
> exposes through the single unified endpoint `MIOS_AI_ENDPOINT`
> (**Architectural Law 5:
> UNIFIED-AI-REDIRECTS**). That endpoint is a *contract*, not a single process:
> the protocol is served by the MiOS inference lanes and agent gateway described
> in [Where the surface is served](#where-the-surface-is-served). Per-endpoint
> *served* status is **deployment-dependent** -- which models and which OpenAI
> sub-surfaces are live depends on which lanes are enabled and which weights are
> provisioned. Verify against the running instance with `GET /v1/models` and the
> per-endpoint probes below before relying on any specific endpoint in
> production.
>
> **Source of truth for the spec:** OpenAI's published reference at
> <https://platform.openai.com/docs/api-reference>. This document tracks that
> surface so any OpenAI-API-compatible client works unchanged against MiOS. When
> MiOS's *served* set diverges from the full spec (e.g. audio/image endpoints may
> not be implemented by the active lane), the divergence is noted in the
> [Compatibility Matrix](#compatibility-matrix), not by omitting the spec.

## Why this doc exists (purpose within the whole system)

MiOS treats its local AI surface the way the rest of the OS treats the
filesystem: one canonical, vendor-neutral interface that every component agrees
on. Architectural Law 5 forbids any agent or tool from hard-coding a vendor URL
or a backend port; they all resolve `MIOS_AI_ENDPOINT` instead. That single
indirection is what lets MiOS swap inference engines (llama.cpp, SGLang, vLLM),
add cluster nodes, or page KV-cache between lanes **without touching a single
client**. This document is the protocol that endpoint speaks. It is the
reference an agent reads to know *how to ask*, and the reference a contributor
reads to know *what MiOS promises to answer*.

The full MiOS AI pipeline -- query refinement, multi-agent fan-out, web search,
tool-loop execution, pgvector memory recall, and final polish -- is layered
*above* this protocol by the [agent-pipe and MiOS-Hermes gateway](#where-the-surface-is-served).
Those orchestration concerns are documented in the concepts/ and guides/ trees;
here we specify only the OpenAI-compatible wire surface they (and external
clients) consume.

For the bounded self-hosting management surface layered alongside this contract,
see [MiOS-API architecture](../concepts/mios-api-architecture.md).

## Capability inventory

`GET /v1/capabilities` returns the caller-filtered, SSOT-projected inventory of
MiOS verbs, recipes, and structured skills. `GET /v1/capabilities/dag` returns
their dependency graph. These resources are the discovery surface for clients
and federated peers; they do not execute an operation.

## Where the surface is served

`MIOS_AI_ENDPOINT` is the logical front door. Behind it, the OpenAI-compatible
protocol is implemented by MiOS's function-named inference lanes and agent
gateway -- each speaks `/v1` natively, so a client never needs to know which one
answered:

| Lane / service | `[ports]` key | Role |
|---|---|---|
| **mios-llm-light** (`mios-llm-light.container`) | `llm_light` | **Primary** local engine -- llama.cpp behind the `mios-llm-light` proxy image (`ghcr.io/mostlygeek/llama-swap`). On-demand multi-model auto-swap behind one OpenAI `/v1` endpoint, per-conversation KV-cache paging to disk (`--slot-save-path`), **and embeddings** (`nomic-embed-text` via `POST /v1/embeddings`). Also serves the `mios-opencode` coder model. Model map: `usr/share/mios/llamacpp/mios-llm-light.yaml`. |
| **mios-llm-heavy** (`mios-llm-heavy.container`) | `vllm` | Heavy GPU reasoning lane -- vLLM (PagedAttention + APC). Serves `mios-heavy` with continuous batching and automatic prefix caching. **gated / off by default** (VRAM); enable only on a dGPU with headroom after baking weights. |
| **mios-llm-worker@** (`mios-llm-worker@.container`) | -- | Single-model swarm workers for parallel fan-out. |
| **MiOS-Hermes gateway** (`mios-gateway-agent.service`) | `hermes` | OpenAI-compatible *agent* gateway -- sessions, tool-calling, skills. Forwards raw inference to the light lane (`llm_light`). |
| **MiOS agent-pipe** (`mios-agent-pipe.service`) | `agent_pipe` | The orchestrator that fronts every gateway (OWUI, Discord, future Slack/Telegram): refine -> multi-agent dispatch -> tool-loop -> pgvector recall -> polish. |
| **MiOS-Prefilter** | `prefilter` | Injects `tool_choice=delegate_task` on fan-outable prompts and forwards to Hermes. |
| **MiOS-OWUI** (`mios-open-webui.container`) | `open_webui` | Open WebUI browser front-end; its `OPENAI_API_BASE_URL` points at the agent surface, so the front-end serves the *agent*, not raw inference. |
| **MiOS-Search** (`mios-searxng.container`) | `searxng` | SearXNG metasearch that backs the `web_search` tool. |
| **mios-opencode-gateway** | `opencode_gateway` | opencode -> OpenAI `/v1` shim, making opencode a first-class council peer (loopback only). |
| **mios-pgvector** (`mios-pgvector.container`) | `pgvector` (in-container 5432) | PostgreSQL + `pgvector` -- the unified agent-plane datastore (memory, events, tool calls, sessions, skills, scratch, knowledge, embeddings). |

The resolved numbers behind those keys come from the `[ports]` SSOT:

<!-- MIOS-GEN:ports:inference -->
| Category | Service | Port |
|---|---|---|
| inference | llm_light | 8500 |
| inference | cpu_node | 8510 |
| inference | llm_heavy | 8520 |
| inference | llm_igpu | 8540 |
| inference | rpc_igpu | 8550 |

<!-- derived from usr/share/mios/mios.toml [ports.categories] -->
<!-- /MIOS-GEN:ports:inference -->
<!-- MIOS-GEN:ports:agent -->
| Category | Service | Port |
|---|---|---|
| agent | agent_pipe | 8700 |
| agent | prefilter | 8710 |
| agent | hermes | 8720 |
| agent | daemon_agent | 8740 |
| agent | model_router | 8750 |
| agent | arbiter | 8760 |
| agent | mcp | 8770 |
| agent | opencode_gateway | 8780 |

<!-- derived from usr/share/mios/mios.toml [ports.categories] -->
<!-- /MIOS-GEN:ports:agent -->
<!-- MIOS-GEN:ports:data -->
| Category | Service | Port |
|---|---|---|
| data | pgvector | 8600 |
| data | pgvector_internal (pinned) | 5432 |

<!-- derived from usr/share/mios/mios.toml [ports.categories] -->
<!-- /MIOS-GEN:ports:data -->
<!-- MIOS-GEN:ports:webtools -->
| Category | Service | Port |
|---|---|---|
| webtools | searxng | 8800 |
| webtools | crawl4ai | 8810 |
| webtools | firecrawl | 8820 |
| webtools | searxng_internal (pinned) | 8080 |

<!-- derived from usr/share/mios/mios.toml [ports.categories] -->
<!-- /MIOS-GEN:ports:webtools -->

> **Migration note (current).** Earlier builds described an Ollama inference
> container and a the legacy datastore/Qdrant agent store. Those are **retired**: inference
> and embeddings now run on the `mios-llm-*` lanes above, and the agent datastore
> is PostgreSQL + `pgvector` (`usr/share/mios/postgres/schema-init.sql`). Ollama
> survives in this doc only as an *upstream API-compatibility reference* -- the
> MiOS lanes speak the same OpenAI/Ollama-compatible `/v1` protocol -- never as a
> live MiOS backend. vLLM / other engines remain *optional* backends,
> off by default, flipped on in `mios.toml [ai]`.

## Conventions

### Base URL
```
$MIOS_AI_ENDPOINT                    # in-image (unified contract; resolves to the hermes port)
http://<host>:<[ports].hermes>/v1    # cross-host
```
All clients (CLI tools, the `mios` agent, IDE plugins, the desktop UI) MUST
resolve through `MIOS_AI_ENDPOINT` -- vendor-hardcoded URLs are a Law-5 violation
and fail audit (`automation/99-postcheck.sh`). The endpoint is the contract; the
served port behind it (the agent gateway on `hermes`, the light lane on `llm_light`,
or a reverse-proxy in front) is an implementation detail clients never
encode.

### Authentication
OpenAI's protocol expects `Authorization: Bearer <token>` on every request.
OpenAI-compatible local servers (the mios-llm-light-fronted light lane, SGLang,
vLLM) accept any non-empty token by default. 'MiOS' deployments SHOULD set a
non-trivial bearer (via the lane's `Environment=API_KEY=...` / a Quadlet
drop-in, or by gating at a reverse-proxy) and require it; the spec below assumes
the token is supplied even where it is currently a no-op.

```
Authorization: Bearer ${MIOS_AI_API_KEY}
Content-Type:  application/json
```

### Versioning
All paths under `/v1`. Newer surfaces (Responses API, Realtime) are also `/v1`.
Beta/admin surfaces require additional headers -- noted per-endpoint.

### Pagination
List endpoints accept `limit` (default 20, max 100), `order` (`asc`|`desc`),
`after`, `before`. Responses include `data[]`, `first_id`, `last_id`,
`has_more`.

### Errors
Standard error envelope:
```json
{
  "error": {
    "message": "string",
    "type":    "invalid_request_error | authentication_error | rate_limit_error | api_error | tools_error",
    "param":   "string | null",
    "code":    "string | null"
  }
}
```
HTTP 4xx/5xx mirror REST conventions. Streamed errors arrive as `event: error`
SSE frames.

### Streaming
Endpoints supporting `stream: true` emit Server-Sent Events with `data:` lines
terminated by `data: [DONE]`. Realtime uses WebSocket (or WebRTC for audio).

## Compatibility Matrix

`'MiOS' Status` values:
- `Unverified` -- endpoint defined in this spec but not yet probed against the
  active lane. Default until the [verification protocol](#verification-protocol-post-build) runs.
- `Served` -- confirmed reachable and protocol-conformant on the active lane.
- `Proxied` -- request is forwarded to an upstream that may or may not be
  MiOS-resident.
- `Unsupported` -- definitively not implemented by the active lane; client SHOULD
  short-circuit.

| Group | Endpoint | 'MiOS' Status |
|---|---|---|
| Models | `GET /v1/models`, `GET /v1/models/{model}`, `DELETE /v1/models/{model}` | Unverified |
| Chat | `POST /v1/chat/completions` | Unverified |
| Completions (legacy) | `POST /v1/completions` | Unverified |
| Responses | `POST /v1/responses`, `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`, `GET /v1/responses/{id}/input_items` | Unverified |
| Embeddings | `POST /v1/embeddings` | Unverified |
| Audio | `POST /v1/audio/transcriptions`, `POST /v1/audio/translations`, `POST /v1/audio/speech` | Unverified |
| Images | `POST /v1/images/generations`, `POST /v1/images/edits`, `POST /v1/images/variations` | Unverified |
| Files | `POST /v1/files`, `GET /v1/files`, `GET /v1/files/{id}`, `DELETE /v1/files/{id}`, `GET /v1/files/{id}/content` | Unverified |
| Uploads | `POST /v1/uploads`, `POST /v1/uploads/{id}/parts`, `POST /v1/uploads/{id}/complete`, `POST /v1/uploads/{id}/cancel` | Unverified |
| Vector Stores | `POST/GET/DELETE /v1/vector_stores`, `.../{id}` | Unverified |
| VS Files | `POST/GET/DELETE /v1/vector_stores/{id}/files[/{file_id}]` | Unverified |
| VS File Batches | `POST/GET /v1/vector_stores/{id}/file_batches[/{batch_id}]`, `.../cancel`, `.../files` | Unverified |
| Assistants | `POST/GET/DELETE /v1/assistants[/{id}]` | Unverified |
| Threads | `POST/GET/DELETE /v1/threads[/{id}]` | Unverified |
| Messages | `POST/GET/DELETE /v1/threads/{tid}/messages[/{mid}]` | Unverified |
| Runs | `POST/GET /v1/threads/{tid}/runs[/{rid}]`, `.../cancel`, `.../submit_tool_outputs` | Unverified |
| Run Steps | `GET /v1/threads/{tid}/runs/{rid}/steps[/{sid}]` | Unverified |
| Batches | `POST/GET /v1/batches[/{id}]`, `.../cancel` | Unverified |
| Fine-tuning | `POST/GET /v1/fine_tuning/jobs[/{id}]`, `.../cancel`, `.../events`, `.../checkpoints` | Unverified |
| Moderations | `POST /v1/moderations` | Unverified |
| Realtime | `WS /v1/realtime`, `POST /v1/realtime/sessions` | Unverified |
| Admin | `/v1/organization/*` | Unsupported (single-tenant deployment) |

> The MiOS pipeline relies in practice on **Chat Completions** (the primary
> generation path through the light/heavy lanes), **Embeddings** (`nomic-embed-text`
> on `mios-llm-light`, feeding pgvector recall), and function/tool calling.
> Audio, image, assistants/threads/runs, vector-store, and fine-tuning surfaces
> are part of the tracked spec but their served status depends on the active
> lane -- probe before depending on them.

To probe live: `curl -fsS -H "Authorization: Bearer $MIOS_AI_API_KEY" "$MIOS_AI_ENDPOINT/models" | jq '.data[].id'`. If that returns model IDs, chat/embeddings are typically also live.

---

## Models

### `GET /v1/models`
List models the deployment can serve. The primary lane (`mios-llm-light`)
advertises the models declared in its `mios-llm-light` model map at
`usr/share/mios/llamacpp/mios-llm-light.yaml`; heavy lanes advertise their single
served name (`mios-heavy`).

Response:
```json
{
  "object": "list",
  "data": [
    { "id": "string", "object": "model", "created": 0, "owned_by": "string" }
  ]
}
```

### `GET /v1/models/{model}`
Single-model lookup.

### `DELETE /v1/models/{model}`
Remove a fine-tuned/uploaded model. Base/system models cannot be deleted.

---

## Chat Completions

### `POST /v1/chat/completions`
Primary text-generation endpoint -- the path the MiOS pipeline drives for every
reasoning turn. Supports tool/function calling, JSON mode, vision (`image_url`
parts), audio (`input_audio` parts), and SSE streaming.

Request:
```jsonc
{
  "model":            "string",                  // required
  "messages":         [Message, ...],            // required
  "max_completion_tokens": 0,                    // newer; replaces max_tokens for o-series
  "max_tokens":       0,                         // legacy; still accepted
  "temperature":      1.0,                       // 0-2
  "top_p":            1.0,
  "n":                1,
  "stream":           false,
  "stream_options":   { "include_usage": false },
  "stop":             "string | string[]",
  "presence_penalty":  0.0,
  "frequency_penalty": 0.0,
  "logit_bias":       { "tokenId": -100 },
  "logprobs":         false,
  "top_logprobs":     0,
  "user":             "string",
  "tools":            [Tool, ...],
  "tool_choice":      "none | auto | required | { type: 'function', function: { name } }",
  "parallel_tool_calls": true,
  "response_format":  { "type": "text | json_object | json_schema",
                        "json_schema": { "name": "...", "strict": true, "schema": { ... } } },
  "seed":             0,
  "service_tier":     "auto | default",
  "store":            false,
  "metadata":         { "key": "value" },
  "reasoning_effort": "low | medium | high"     // o-series
}
```

`Message` shape:
```jsonc
{
  "role":      "system | developer | user | assistant | tool",
  "content":   "string | ContentPart[]",
  "name":      "string",                         // optional
  "tool_calls":[ ToolCall ],                     // assistant only
  "tool_call_id":"string"                        // tool only
}
```

`ContentPart` shapes: `{type:"text",text}`, `{type:"image_url",image_url:{url,detail}}`, `{type:"input_audio",input_audio:{data,format}}`, `{type:"file",file:{file_id|file_data|filename}}`.

`Tool` (function-calling):
```json
{ "type":"function",
  "function":{ "name":"string", "description":"string",
               "parameters":{ /* JSON Schema */ },
               "strict": true } }
```

Response (non-stream):
```jsonc
{
  "id":"chatcmpl-...", "object":"chat.completion", "created":0, "model":"string",
  "choices":[
    { "index":0,
      "message":{ "role":"assistant", "content":"string|null", "tool_calls":[...], "refusal":null },
      "logprobs":null,
      "finish_reason":"stop|length|tool_calls|content_filter|function_call" }
  ],
  "usage":{ "prompt_tokens":0,"completion_tokens":0,"total_tokens":0,
            "completion_tokens_details":{"reasoning_tokens":0} },
  "system_fingerprint":"string"
}
```

Streaming: SSE with `chat.completion.chunk` objects whose `choices[0].delta` carries incremental `content`/`tool_calls` deltas; final frame is `data: [DONE]`.

Probe (use a `model` id the active lane advertises -- e.g. a tag from the
light lane's `mios-llm-light.yaml`, or `mios-heavy` for an enabled heavy lane):
```bash
curl -fsS -H "Authorization: Bearer $MIOS_AI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"'"$(curl -fsS -H "Authorization: Bearer $MIOS_AI_API_KEY" "$MIOS_AI_ENDPOINT/models" | jq -r '.data[0].id')"'","messages":[{"role":"user","content":"ping"}]}' \
  "$MIOS_AI_ENDPOINT/chat/completions"
```

---

## Completions (Legacy)

### `POST /v1/completions`
Single-prompt text generation. Retained for legacy compatibility; new code SHOULD use `/chat/completions` or `/responses`.

Request: `{ model, prompt, max_tokens, temperature, top_p, n, stream, logprobs, echo, stop, presence_penalty, frequency_penalty, best_of, logit_bias, user, suffix }`.

Response: `{ id, object:"text_completion", created, model, choices:[{ text, index, logprobs, finish_reason }], usage }`.

---

## Responses

The Responses API unifies chat, tool-use, vision, file/image inputs, and stateful conversations into one endpoint. Newer than `/chat/completions`; SHOULD be the default for new 'MiOS' agent code where the active lane supports it.

### `POST /v1/responses`
Request:
```jsonc
{
  "model": "string",
  "input": "string | InputItem[]",                 // text or structured items
  "instructions": "string",                        // system-style preamble
  "previous_response_id": "string",                // chain prior turn for state reuse
  "tools": [ Tool, ... ],                          // function | file_search | web_search_preview | computer_use | ...
  "tool_choice": "auto | none | required | { ... }",
  "parallel_tool_calls": true,
  "stream": false,
  "stream_options": { "include_obfuscation": false },
  "include": [ "file_search_call.results", "message.input_image.image_url",
               "computer_call_output.output.image_url",
               "reasoning.encrypted_content" ],
  "max_output_tokens": 0,
  "temperature": 1.0,
  "top_p": 1.0,
  "metadata": { "key":"value" },
  "store": true,
  "background": false,
  "reasoning": { "effort":"low|medium|high", "summary":"auto|concise|detailed" },
  "text": { "format":{ "type":"text|json_schema|json_object", "json_schema":{ ... } } },
  "truncation": "auto | disabled",
  "service_tier": "auto | default | flex"
}
```

`InputItem` types: `message`, `file_search_call`, `function_call`, `function_call_output`, `computer_call`, `computer_call_output`, `web_search_call`, `reasoning`, `input_image`, `input_file`.

Response:
```jsonc
{
  "id":"resp_...", "object":"response", "created_at":0, "model":"string",
  "status":"completed | in_progress | incomplete | failed | cancelled",
  "output":[ /* ordered list of output items: messages, tool calls, reasoning */ ],
  "output_text":"string",                          // convenience flatten
  "usage":{ "input_tokens":0,"output_tokens":0,"total_tokens":0,
            "input_tokens_details":{"cached_tokens":0},
            "output_tokens_details":{"reasoning_tokens":0} },
  "incomplete_details": null,
  "error": null,
  "metadata": { ... },
  "previous_response_id": null,
  "parallel_tool_calls": true,
  "store": true
}
```

### `GET /v1/responses/{response_id}`
Fetch a stored Response.

### `DELETE /v1/responses/{response_id}`
Delete a stored Response.

### `GET /v1/responses/{response_id}/input_items`
Paginated list of input items the Response was created with.

### `POST /v1/responses/{response_id}/cancel`
Cancel a `background:true` Response in progress.

---

## Embeddings

### `POST /v1/embeddings`
Generate dense vectors. In MiOS this is served by the **primary lane**
(`mios-llm-light`, port key `llm_light`) running `nomic-embed-text` with
`llama-server --embedding` -- the same `/v1/embeddings` surface the OpenAI SDKs
expect. It feeds the agent-plane's vector memory (pgvector recall, knowledge
ingestion).

Request:
```json
{
  "model": "string",
  "input": "string | string[] | int[] | int[][]",
  "encoding_format": "float | base64",
  "dimensions": 0,
  "user": "string"
}
```
Response:
```json
{ "object":"list",
  "data":[{ "object":"embedding","index":0,"embedding":[0.0, ...] }],
  "model":"string",
  "usage":{ "prompt_tokens":0,"total_tokens":0 } }
```

'MiOS' cross-ref: embeddings returned here are persisted into the
`pgvector`-backed agent datastore (`mios-pgvector`, schema
`usr/share/mios/postgres/schema-init.sql`) and used for cosine recall of prior
answers and ingested knowledge. The legacy chunk-export format lives at
`var/lib/mios/embeddings/chunks.jsonl`.

---

## Audio

### `POST /v1/audio/transcriptions`
Multipart form. Fields: `file` (audio binary), `model`, `language`, `prompt`, `response_format` (`json | text | srt | vtt | verbose_json`), `temperature`, `timestamp_granularities[]` (`word | segment`).

Response (`json` default): `{ "text": "string" }`. Verbose: includes `segments[]` with `start`, `end`, `text`, `tokens`, `avg_logprob`, `compression_ratio`, `no_speech_prob`.

### `POST /v1/audio/translations`
Same shape as transcriptions; output is English regardless of input language.

### `POST /v1/audio/speech`
Request:
```json
{
  "model":"string", "voice":"alloy|echo|fable|onyx|nova|shimmer|coral|ash|ballad|sage|verse|...",
  "input":"string", "response_format":"mp3|opus|aac|flac|wav|pcm",
  "speed":1.0, "instructions":"string"
}
```
Response: binary audio stream of the requested format. Streaming via `Transfer-Encoding: chunked`.

---

## Images

### `POST /v1/images/generations`
Request:
```json
{
  "model":"string", "prompt":"string",
  "n":1, "size":"auto|256x256|512x512|1024x1024|1024x1536|1536x1024|1792x1024|1024x1792",
  "quality":"auto|standard|hd|low|medium|high",
  "background":"auto|transparent|opaque",
  "moderation":"auto|low",
  "output_format":"png|jpeg|webp",
  "output_compression":0,
  "response_format":"url|b64_json",
  "style":"vivid|natural",
  "user":"string"
}
```
Response: `{ "created":0, "data":[ { "url"|"b64_json", "revised_prompt" } ] }`.

### `POST /v1/images/edits`
Multipart. `image` (PNG, ≤4MB, square for some models), optional `mask`, `prompt`, plus the generation params above.

### `POST /v1/images/variations`
Multipart. `image`, `model`, `n`, `size`, `response_format`.

---

## Files

### `POST /v1/files`
Multipart form. `file` (any binary), `purpose` (`assistants | batch | fine-tune | vision | user_data | evals`).

Response: `File` object -- `{ id, object:"file", bytes, created_at, filename, purpose, status, expires_at, status_details }`.

### `GET /v1/files`
Query: `purpose`, `limit`, `order`, `after`. Returns paginated `File[]`.

### `GET /v1/files/{file_id}`
Single-file metadata.

### `DELETE /v1/files/{file_id}`
Delete an uploaded file. Returns `{ id, object:"file", deleted:true }`.

### `GET /v1/files/{file_id}/content`
Download the file body. `Content-Type` reflects original.

---

## Uploads

Multi-part upload protocol for files larger than the single-shot 512MB ceiling. Use when ingesting model weights, datasets, or large eval artifacts.

### `POST /v1/uploads`
`{ purpose, filename, bytes, mime_type }` → `Upload { id, status:"pending", expires_at }`.

### `POST /v1/uploads/{upload_id}/parts`
Multipart with `data` field; max 64MB/part, max 250 parts. Response: `UploadPart { id, upload_id, created_at }`.

### `POST /v1/uploads/{upload_id}/complete`
`{ part_ids: ["..."], md5: "..." }` → finalized `Upload` containing a `file` field referencing the assembled `File`.

### `POST /v1/uploads/{upload_id}/cancel`
Abort and free server-side state.

---

## Vector Stores

> **MiOS implementation note.** MiOS's *native* vector memory is PostgreSQL +
> `pgvector` (`mios-pgvector`, HNSW indexing) -- not the OpenAI vector-store
> objects below. The agent-pipe stores and recalls embeddings directly in
> pgvector. The OpenAI vector-store surface is tracked here for SDK
> compatibility; whether the active lane serves it is deployment-dependent.

### `POST /v1/vector_stores`
`{ name, file_ids[], expires_after:{anchor:"last_active_at",days}, chunking_strategy:{type:"auto|static",static:{max_chunk_size_tokens,chunk_overlap_tokens}}, metadata }`. Response: `VectorStore`.

### `GET /v1/vector_stores`
Paginated `VectorStore[]`.

### `GET /v1/vector_stores/{vector_store_id}`
Single-store fetch.

### `POST /v1/vector_stores/{vector_store_id}`
Mutable fields: `name`, `expires_after`, `metadata`.

### `DELETE /v1/vector_stores/{vector_store_id}`
Delete.

`VectorStore` shape: `{ id, object:"vector_store", created_at, name, usage_bytes, file_counts:{ in_progress, completed, failed, cancelled, total }, status:"expired|in_progress|completed", expires_after, expires_at, last_active_at, metadata }`.

### Vector-store search
`POST /v1/vector_stores/{vector_store_id}/search`
`{ query, max_num_results, filters:{ ... }, ranking_options:{ ranker:"auto|default-2024-11-15", score_threshold } }` → `{ object:"vector_store.search_results.page", search_query, data:[ { file_id, filename, score, attributes, content:[ { type:"text", text } ] } ], has_more, next_page }`.

---

## Vector Store Files

### `POST /v1/vector_stores/{vector_store_id}/files`
Attach an uploaded `File` to the store. `{ file_id, chunking_strategy, attributes }`. Response: `VectorStoreFile`.

### `GET /v1/vector_stores/{vector_store_id}/files`
List with filter `?filter=in_progress|completed|failed|cancelled`.

### `GET /v1/vector_stores/{vector_store_id}/files/{file_id}`
Single-file status + chunking details.

### `POST /v1/vector_stores/{vector_store_id}/files/{file_id}`
Update mutable `attributes`.

### `DELETE /v1/vector_stores/{vector_store_id}/files/{file_id}`
Detach (does NOT delete the underlying `File`).

### `GET /v1/vector_stores/{vector_store_id}/files/{file_id}/content`
Stream the parsed/chunked text content the store indexed.

---

## Vector Store File Batches

### `POST /v1/vector_stores/{vector_store_id}/file_batches`
`{ file_ids[], attributes, chunking_strategy }` → `VectorStoreFileBatch { id, status, file_counts, ... }`.

### `GET /v1/vector_stores/{vector_store_id}/file_batches/{batch_id}`
Status fetch.

### `POST /v1/vector_stores/{vector_store_id}/file_batches/{batch_id}/cancel`
Cancel an in-progress batch.

### `GET /v1/vector_stores/{vector_store_id}/file_batches/{batch_id}/files`
Paginated `VectorStoreFile[]` for the batch.

---

## Assistants

### `POST /v1/assistants`
Request:
```jsonc
{
  "model":"string",
  "name":"string","description":"string","instructions":"string",
  "tools":[ {"type":"code_interpreter"}, {"type":"file_search","file_search":{ ... }},
            {"type":"function","function":{ ... }} ],
  "tool_resources":{ "code_interpreter":{ "file_ids":[...] },
                     "file_search":{ "vector_store_ids":[...] } },
  "metadata":{ ... }, "temperature":1.0, "top_p":1.0,
  "response_format":"auto | { type:'text|json_object|json_schema', ... }",
  "reasoning_effort":"low|medium|high"
}
```

### `GET /v1/assistants` / `GET /v1/assistants/{id}` / `POST /v1/assistants/{id}` / `DELETE /v1/assistants/{id}`
Standard list / fetch / modify / delete.

---

## Threads

### `POST /v1/threads`
`{ messages[], tool_resources, metadata }` → `Thread`.

### `GET /v1/threads/{thread_id}` / `POST /v1/threads/{thread_id}` / `DELETE /v1/threads/{thread_id}`
Fetch / modify / delete.

---

## Messages

### `POST /v1/threads/{thread_id}/messages`
`{ role:"user|assistant", content:"string|ContentPart[]", attachments:[{file_id,tools:[...]}], metadata }` → `Message`.

### `GET /v1/threads/{thread_id}/messages` / `GET /v1/threads/{thread_id}/messages/{message_id}` / `POST ...` / `DELETE ...`
List / fetch / modify (metadata only) / delete.

---

## Runs

### `POST /v1/threads/{thread_id}/runs`
Trigger an assistant turn against a thread.
```jsonc
{
  "assistant_id":"string",
  "model":"string",                             // override
  "instructions":"string",                      // override
  "additional_instructions":"string",
  "additional_messages":[ Message ],
  "tools":[ Tool ],                             // override
  "metadata":{ ... },
  "temperature":1.0, "top_p":1.0,
  "stream":false,
  "max_prompt_tokens":0, "max_completion_tokens":0,
  "truncation_strategy":{ "type":"auto|last_messages","last_messages":0 },
  "tool_choice":"auto|none|required|{ type:'function',function:{name} }",
  "parallel_tool_calls":true,
  "response_format":"auto | { ... }",
  "reasoning_effort":"low|medium|high"
}
```
Response: `Run` (status: `queued | in_progress | requires_action | cancelling | cancelled | failed | completed | incomplete | expired`).

### `POST /v1/threads/runs`
Create-thread-and-run combined op. Body merges thread-create and run-create.

### `GET /v1/threads/{thread_id}/runs` / `GET .../{run_id}` / `POST .../{run_id}` (metadata)
Standard.

### `POST /v1/threads/{thread_id}/runs/{run_id}/cancel`
Cancel.

### `POST /v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs`
Reply to `requires_action.submit_tool_outputs`. `{ tool_outputs:[{ tool_call_id, output }], stream }`.

---

## Run Steps

### `GET /v1/threads/{thread_id}/runs/{run_id}/steps`
Paginated `RunStep[]`.

### `GET /v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}`
Single step. `step_details` discriminated by `type`: `message_creation` or `tool_calls` (each tool call typed: `code_interpreter`, `file_search`, `function`).

---

## Batches

Asynchronous bulk request execution. Inputs/outputs are JSONL `File`s.

### `POST /v1/batches`
```json
{
  "input_file_id":"file_...",
  "endpoint":"/v1/chat/completions | /v1/embeddings | /v1/responses | /v1/completions",
  "completion_window":"24h",
  "metadata":{ ... }
}
```
Response: `Batch { id, object:"batch", endpoint, errors, input_file_id, completion_window, status:"validating|failed|in_progress|finalizing|completed|expired|cancelling|cancelled", output_file_id, error_file_id, created_at, in_progress_at, expires_at, finalizing_at, completed_at, failed_at, expired_at, cancelling_at, cancelled_at, request_counts:{ total, completed, failed }, metadata }`.

### `GET /v1/batches/{batch_id}` / `GET /v1/batches`
Status / list.

### `POST /v1/batches/{batch_id}/cancel`
Cancel; status transitions through `cancelling` → `cancelled`.

'MiOS' cross-ref: `usr/share/mios/api/batch.requests.jsonl` is the canonical input format -- one JSON object per line, each `{ custom_id, method, url, body }`.

---

## Fine-tuning

### `POST /v1/fine_tuning/jobs`
```jsonc
{
  "model":"string", "training_file":"file_...", "validation_file":"file_...",
  "method":{ "type":"supervised | dpo | reinforcement",
             "supervised":{ "hyperparameters":{ "n_epochs":"auto|int",
                "batch_size":"auto|int", "learning_rate_multiplier":"auto|number" } },
             "dpo":{ "hyperparameters":{ "beta":"auto|number", ... } },
             "reinforcement":{ "hyperparameters":{ ... }, "grader":{ ... } } },
  "suffix":"string", "seed":0,
  "integrations":[ { "type":"wandb", "wandb":{ "project","name","entity","tags":[] } } ],
  "metadata":{ ... }
}
```
Response: `FineTuningJob`.

### `GET /v1/fine_tuning/jobs` / `GET .../{id}`
List / fetch. Status: `validating_files | queued | running | succeeded | failed | cancelled`.

### `POST /v1/fine_tuning/jobs/{id}/cancel`
Cancel.

### `GET /v1/fine_tuning/jobs/{id}/events`
Paginated `FineTuningJobEvent[]` (`object:"fine_tuning.job.event"`).

### `GET /v1/fine_tuning/jobs/{id}/checkpoints`
Paginated `FineTuningJobCheckpoint[]` -- references intermediate models that can be used as `model` in a chat-completions request.

### `POST /v1/fine_tuning/checkpoints/{permission_id}/permissions` (Admin)
Grant per-checkpoint sharing. Out of scope for single-tenant MiOS.

'MiOS' cross-ref: MiOS's own LoRA/SFT fine-tune subsystem produces datasets in
the formats this endpoint expects -- `var/lib/mios/training/sft.jsonl`
(supervised) and `var/lib/mios/training/dpo.jsonl` (preference).

---

## Moderations

### `POST /v1/moderations`
```json
{ "model":"omni-moderation-latest|text-moderation-latest", "input":"string | string[] | InputItem[]" }
```
Response: `{ id, model, results:[ { flagged, categories:{ ... }, category_scores:{ ... }, category_applied_input_types:{ ... } } ] }`.

---

## Realtime

### `WS /v1/realtime?model=...`
Bidirectional WebSocket for low-latency speech-to-speech and text-to-speech sessions. Client sends/receives event-typed JSON envelopes; audio frames are base64 PCM16 over `input_audio_buffer.append`. Major event types:

- Client → server: `session.update`, `input_audio_buffer.{append,commit,clear}`, `conversation.item.{create,truncate,delete}`, `response.{create,cancel}`.
- Server → client: `session.{created,updated}`, `input_audio_buffer.{committed,cleared,speech_started,speech_stopped}`, `conversation.item.{created,input_audio_transcription.{completed,failed},truncated,deleted}`, `response.{created,done,output_item.{added,done},content_part.{added,done},text.{delta,done},audio_transcript.{delta,done},audio.{delta,done},function_call_arguments.{delta,done}}`, `rate_limits.updated`, `error`.

### `POST /v1/realtime/sessions`
Mint an ephemeral client token (`client_secret.value`) for browser-side WebRTC connections without exposing `MIOS_AI_API_KEY`. Body mirrors the `session.update` shape. Response includes `client_secret.expires_at`.

### `POST /v1/realtime/transcription_sessions`
Same pattern, scoped to transcription-only sessions.

---

## Admin / Organization

`/v1/organization/*` covers user/project/api-key administration, audit logs, costs, and usage. **'MiOS' Status: Unsupported** -- MiOS is a single-tenant deployment; auth is enforced at the lane/reverse-proxy layer. If multi-tenant becomes a goal, document the chosen subset in a follow-up `ADMIN.md`.

---

## Error Codes

| Code | HTTP | Meaning |
|---|---|---|
| `invalid_request_error` | 400 | Malformed body, unknown field, bad enum value. |
| `authentication_error` | 401 | Missing/invalid `Authorization` header. |
| `permission_error` | 403 | Token lacks scope; deployment policy block. |
| `not_found_error` | 404 | Unknown id (file, model, response, etc.). |
| `conflict_error` | 409 | Concurrent-modification conflict on a stateful object. |
| `unprocessable_entity_error` | 422 | Schema-valid but logically rejected. |
| `rate_limit_error` | 429 | TPM/RPM/quota exceeded. Retry with backoff. |
| `api_error` | 500 | Transient server fault. |
| `bad_gateway_error` | 502 | Upstream model unavailable. |
| `service_unavailable_error` | 503 | Capacity exhaustion. |
| `gateway_timeout_error` | 504 | Generation exceeded wall-clock budget. |

Streamed errors arrive as `event: error` SSE frames with the same envelope.

---

## Rate Limits

OpenAI advertises `x-ratelimit-{limit,remaining,reset}-{requests,tokens}` headers. The behavior of local lanes (llama.cpp/mios-llm-light, SGLang, vLLM) depends on configuration; 'MiOS' deployments SHOULD enforce limits at the lane/reverse-proxy layer if multi-client. Until that's wired, treat headers as advisory. (In practice the agent-pipe governs concurrency upstream of the lanes via per-lane semaphores and a VRAM-aware admission controller -- see `mios.toml [ai]`.)

---

## SDKs

The official OpenAI SDKs (`openai-python`, `openai-node`, `openai-go`, `openai-java`) all accept a `base_url` override. Because MiOS speaks the OpenAI protocol on the unified endpoint, **any OpenAI-API-compatible editor or CLI client works unchanged** -- no vendor lock-in. 'MiOS' clients SHOULD construct clients with:

```python
from openai import OpenAI, AsyncOpenAI
import os
client = OpenAI(base_url=os.environ["MIOS_AI_ENDPOINT"],
                api_key=os.environ.get("MIOS_AI_API_KEY", "no-auth"))
```

```javascript
import OpenAI from "openai";
const client = new OpenAI({
  baseURL: process.env.MIOS_AI_ENDPOINT,
  apiKey:  process.env.MIOS_AI_API_KEY ?? "no-auth"
});
```

```bash
export OPENAI_BASE_URL="$MIOS_AI_ENDPOINT"
export OPENAI_API_KEY="$MIOS_AI_API_KEY"
# every CLI tool that respects OPENAI_BASE_URL now points at MiOS.
```

---

## Cross-references

- Architectural Law 5 (UNIFIED-AI-REDIRECTS): [`CLAUDE.md`](CLAUDE.md), [`usr/share/mios/ai/INDEX.md`](usr/share/mios/ai/INDEX.md), [`usr/share/doc/mios/guides/engineering.md`](usr/share/doc/mios/guides/engineering.md).
- Primary inference lane / model map: [`usr/share/mios/llamacpp/mios-llm-light.yaml`](usr/share/mios/llamacpp/mios-llm-light.yaml), [`usr/share/containers/systemd/mios-llm-light.container`](usr/share/containers/systemd/mios-llm-light.container).
- Heavy lane (gated): [`usr/share/containers/systemd/mios-llm-heavy.container`](usr/share/containers/systemd/mios-llm-heavy.container) (vLLM).
- Agent datastore (pgvector): [`usr/share/containers/systemd/mios-pgvector.container`](usr/share/containers/systemd/mios-pgvector.container), schema [`usr/share/mios/postgres/schema-init.sql`](usr/share/mios/postgres/schema-init.sql).
- Orchestration pipeline: [`usr/lib/mios/agent-pipe/server.py`](usr/lib/mios/agent-pipe/server.py).
- Batch input format: [`usr/share/mios/api/batch.requests.jsonl`](usr/share/mios/api/batch.requests.jsonl).
- Embedding output / chunk format: [`var/lib/mios/embeddings/chunks.jsonl`](var/lib/mios/embeddings/chunks.jsonl).
- Fine-tuning training data: [`var/lib/mios/training/sft.jsonl`](var/lib/mios/training/sft.jsonl), [`var/lib/mios/training/dpo.jsonl`](var/lib/mios/training/dpo.jsonl).
- Eval datasets: [`var/lib/mios/evals/dataset.jsonl`](var/lib/mios/evals/dataset.jsonl).
- Upstream spec source: <https://platform.openai.com/docs/api-reference>.

---

## Verification protocol (post-build)

To flip every endpoint's `'MiOS' Status` from `Unverified` → `Served | Proxied | Unsupported`, run from inside the deployed 'MiOS' instance against the active lane:

```bash
# Required: instance must have curl + jq.
export E="$MIOS_AI_ENDPOINT" K="${MIOS_AI_API_KEY:-no-auth}"
H=(-H "Authorization: Bearer $K" -H "Content-Type: application/json")

# Probe each surface; record HTTP status into a manifest the matrix can consume.
{
  for path in models chat/completions completions responses embeddings \
              audio/speech images/generations files uploads vector_stores \
              assistants threads batches fine_tuning/jobs moderations \
              realtime/sessions; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" \
                -X $( [[ "$path" == models ]] && echo GET || echo POST ) \
                ${path:+-d '{}'} \
                "$E/$path")
    printf '%-30s %s\n' "$path" "$code"
  done
} > /usr/lib/mios/logs/openai-api-verification.txt
```

Update the [Compatibility Matrix](#compatibility-matrix) from the resulting file. A `405` on a `GET`-not-`POST` endpoint also confirms the route exists; `404` confirms unsupported; `401` confirms route exists but auth is enforced.

---

# Appendix: MiOS Build & Architecture Reference

This appendix consolidates the build/architecture invariants. `CLAUDE.md` is the
thin per-tool agent-identity overlay (it defers to `/MiOS.md` for runtime
identity); the authoritative MiOS-internal reference lives here so agents have a
single canonical source. It exists so an agent reading *only this API doc* still
understands the system the API lives inside.

> Canonical agent prompt: `/usr/share/mios/ai/system.md` (deployed from `mios-bootstrap`).
> Loading order: `/usr/share/mios/ai/system.md` -> `/etc/mios/ai/system-prompt.md` (host override) -> `~/.config/mios/system-prompt.md` (user override).

## A.1 What this repo is

'MiOS' is an immutable, `bootc`-managed Fedora-derived workstation OS distributed as an OCI image -- and at the same time a local, self-replicating agentic AI OS. The repo root **is** the deployed system root: `usr/`, `etc/`, `srv/`, `var/`, `proc/`, `opt/` at the top level mirror their FHS-3.0 destinations. There is no `system_files/` indirection; `automation/01-system-files-overlay.sh` overlays them into the image.

The published image is `ghcr.io/mios-dev/mios:latest` and is built `FROM ghcr.io/ublue-os/ucore-hci:stable-nvidia` (set via `MIOS_BASE_IMAGE`). The build pipeline produces the image; `bootc` manages its lifecycle (`bootc upgrade` to roll forward, `bootc rollback` to undo); and the AI stack documented above is one of the curated layers baked into that image.

## A.2 Build commands

Linux orchestrator is `Justfile`; Windows orchestrator is `mios-build-local.ps1`. (The legacy `cloud-ws.ps1` / CloudWS naming is retired -- the Windows path is `mios-build-local.ps1`.) There is no four-stage pipeline.

```bash
just preflight    # System prereq check (mios-probe, thresholds from [preflight])
just build        # Build OCI image -> localhost/mios:latest
just lint         # Re-run `bootc container lint` on the built image
just rechunk      # Optimize Day-2 deltas (rechunk into versioned tag)
just raw          # RAW disk image via BIB
just iso          # Anaconda installer ISO via BIB
just qcow2        # Requires MIOS_USER_PASSWORD_HASH env (openssl passwd -6)
just vhdx         # Hyper-V VHDX (same env requirement)
just wsl2         # WSL2 tarball
just sbom         # CycloneDX SBOM via syft
just artifact     # Refresh AI manifests, UKB, and Wiki docs
just all-bootstrap # build + rechunk + log to bootstrap repo
```

Windows: `.\preflight.ps1` then `.\mios-build-local.ps1` (rootful Podman machine, credential injection, BIB, GHCR push, cleanup).

The `Containerfile` already runs `bootc container lint` as its final RUN -- `just build` is itself the lint gate.

## A.3 Phase-2 build pipeline (the `automation/` directory)

`Containerfile` triggers `automation/build.sh`, which iterates every `automation/[0-9][0-9]-*.sh` in lexicographic numeric order. **Sub-phase numbering encodes dependency order and must be preserved when adding new scripts.** Per-script failures are captured in `FAIL_LOG`/`WARN_LOG` (set +e wrapper around each invocation) -- the orchestrator does not abort. Critical packages are post-validated via `rpm -q`.

Notable skips under the in-Containerfile build:
- `01-system-files-overlay.sh` -- runs pre-pipeline directly from `Containerfile`.
- Model-bake steps (the inference-lane weight bakes, e.g. SGLang/vLLM prep) are **opt-in** and CI-skipped, so a default CI build never bloats the image with multi-GB weights. (The former `37-ollama-prep.sh` ollama bake is retired along with Ollama itself.)

The full pipeline spans five phases owned by two repos:

| Phase | Owner | Description |
|---|---|---|
| 0 | `mios-bootstrap.git/install.sh` | Preflight + profile load + identity capture |
| 1 | `mios-bootstrap.git/install.sh` | Total Root Merge of `mios.git` and `mios-bootstrap.git` to `/` |
| 2 | `Containerfile` + `automation/build.sh` | Build (this repo) |
| 3 | `mios.git/install.sh` + bootstrap profile staging | sysusers/tmpfiles + user create + per-user `~/.config/mios/{profile.toml,system-prompt.md}` |
| 4 | `mios-bootstrap.git/install.sh` | Reboot prompt |

## A.4 Architectural Laws (non-negotiable, build/audit-fail on violation)

1. **USR-OVER-ETC** -- static config in `/usr/lib/<component>.d/`; `/etc/` is admin-override only. Documented exceptions are upstream-contract surfaces (`/etc/yum.repos.d/`, `/etc/nvidia-container-toolkit/`).
2. **NO-MKDIR-IN-VAR** -- every `/var/` path declared via `usr/lib/tmpfiles.d/*.conf`. **Never write to `/var/` at build time.** bootc forbids it; lint will fail.
3. **BOUND-IMAGES** -- every Quadlet image symlinked into `/usr/lib/bootc/bound-images.d/`. Binder loop: `automation/01-system-files-overlay.sh`.
4. **BOOTC-CONTAINER-LINT** -- must be the final `RUN` of `Containerfile`. No `--squash-all` (strips OCI metadata bootc needs).
5. **UNIFIED-AI-REDIRECTS** -- all agents and tools target `MIOS_AI_ENDPOINT`. Vendor-hardcoded URLs are forbidden. The endpoint is served by the MiOS inference lanes (`mios-llm-light` primary; `mios-llm-heavy` gated) fronted by the MiOS-Hermes gateway (`hermes`) and the agent-pipe (`agent_pipe`) -- see [Where the surface is served](#where-the-surface-is-served).
6. **UNPRIVILEGED-QUADLETS** -- every Quadlet declares `User=`, `Group=`, `Delegate=yes`. Documented root exceptions: `mios-ceph`, `mios-k3s`, `mios-forgejo-runner` (file headers explain why; heavy-lane images that must run as image-default root, e.g. SGLang's NVIDIA base, are similarly documented in their unit headers).

## A.5 Package management

Single source of truth: `usr/share/mios/mios.toml` under `[packages.<section>].pkgs` (human-readable rationale at `usr/share/doc/mios/reference/PACKAGES.md`). Every RPM is parsed by `automation/lib/packages.sh`. **Never call `dnf install` on hard-coded names.** Use:

- `install_packages "<category>"` -- best-effort, `--skip-unavailable`
- `install_packages_strict "<category>"` -- fails the script on any miss
- `install_packages_optional "<category>"` -- pure best-effort, never fails

Kernel rule: only add `kernel-modules-extra`, `kernel-devel`, `kernel-headers`, `kernel-tools`. Never upgrade `kernel`/`kernel-core` in-container -- `automation/01-repos.sh` excludes them. dnf option spelling is `install_weak_deps=False` (underscore); `install_weakdeps` is silently ignored by dnf5.

## A.6 Containerfile shape

Single-stage main image with a `ctx` scratch context that bind-mounts read-only at `/ctx`. Mutating writes go to `/tmp/build`. The `Containerfile` pre-pipeline `RUN` installs `packages-base` (security stack) before `automation/build.sh` runs.

## A.7 Shell conventions

- `set -euo pipefail` at the top of every phase script.
- Arithmetic: `VAR=$((VAR + 1))`. **`((VAR++))` is forbidden** -- under `set -e` it exits 1 when the result is 0.
- shellcheck-clean. SC2038 is fatal in CI (`.github/workflows/mios-ci.yml`).
- File naming: `NN-name.sh` where NN encodes execution order.

## A.8 Kargs format

`usr/lib/bootc/kargs.d/*.toml` uses a flat top-level array; bootc rejects anything else:

```toml
kargs = ["init_on_alloc=1", "lockdown=integrity"]
```

No `[kargs]` section header, no `delete` sub-key. Files processed in lexicographic order; earlier entries cannot be removed by later files in the same image -- use runtime `bootc kargs --delete` for removal.

Note: `lockdown=integrity` (not `confidentiality`). `init_on_alloc=1`, `init_on_free=1`, `page_alloc.shuffle=1` are **disabled** in 'MiOS' due to NVIDIA/CUDA incompatibility.

## A.9 Service gating

- Bare-metal-only services: `ConditionVirtualization=no` drop-in.
- WSL2-incompatible: `ConditionVirtualization=!wsl`.
- Optional: `systemctl enable ... || true`.

Every boolean in `usr/share/mios/profile.toml` ships **`true`**; the system never disables a component via static config -- Quadlet `Condition*` directives short-circuit incompatible units silently. (This is how the gated heavy inference lanes stay inert until weights are baked and a dGPU has headroom.)

## A.10 Agent operating context

- **cwd:** `/` is both the repo root and the deployed system root -- do not treat it as dangerous.
- **Confirm before:** `git push`, `bootc upgrade`, `dnf install`, `systemctl`, `rm -rf`.
- **Deliverables:** complete replacement files only -- no diffs, no patches, no "paste this into X" fragments. Nothing in the repo gets removed without prior discussion.
- **Memory:** `/var/lib/mios/ai/memory/` (durable agent memory also persists in the `mios-pgvector` agent datastore).
- **Scratch:** `/var/lib/mios/ai/scratch/`
- **Tasks:** use the task tool for multi-step work; one in-progress at a time.

## A.11 Cross-references

- Architectural laws and API surface: `usr/share/mios/ai/INDEX.md`
- Filesystem and hardware layout: `usr/share/doc/mios/concepts/architecture.md`
- Engineering standards (authoritative source for build rules): `usr/share/doc/mios/guides/engineering.md`
- Build modes: `usr/share/doc/mios/guides/self-build.md`
- Deployment and Day-2 lifecycle: `usr/share/doc/mios/guides/deploy.md`
- Security posture and hardening kargs: `usr/share/doc/mios/guides/security.md`
- Contribution conventions: `CONTRIBUTING.md`
