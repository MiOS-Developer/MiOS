<!-- AI-hint: Documentation for ingesting the 'MiOS' knowledge base into any OpenAI-API-compatible runtime; procedures for local inference (mios-llm-light, port key `llm_light`), pgvector RAG ingestion, and evals — grounded in the unified MIOS_AI_ENDPOINT abstraction.
     AI-related: /usr/share/mios/api/chat.local.example.json, mios-ai, mios-env, mios-kb, mios-knowledge, mios-llm-light, mios-pgvector, mios-agent-pipe, ports.agent_pipe, ports.hermes, ports.llm_light, localhost:5432 -->
# usr/share/doc/mios/guides/install.md — Ingest the 'MiOS' KB into any OpenAI-API-compatible runtime

## Purpose and place in the system

MiOS is one thing built two ways at once: an immutable, bootc/OCI-shaped Fedora
workstation (the whole OS is a single container image you `bootc upgrade` like a
`git pull` and `bootc rollback` like a Ctrl-Z) that is *also* a local,
self-replicating, agentic AI operating system. The agentic half runs entirely
on-box: a request flows from a front-end (OWUI, the Discord gateway, the `mios`
CLI) into the **agent-pipe** orchestrator (port key `agent_pipe`), which refines and fans it
out across a council/swarm and dispatches tool/verb calls; **MiOS-Hermes**
(port key `hermes`) is the OpenAI-compatible gateway and tool-loop agent; **pgvector**
(`:5432`) is the unified agent memory (tiered memory, knowledge, sessions,
skills, RAG embeddings); and the **inference lanes** do the actual generation
and embeddings behind one OpenAI-compatible surface.

This guide's job is narrow within that whole: it shows how to take the **MiOS
knowledge base** (the chunked, embeddable corpus describing MiOS itself) and
make it queryable from *any* OpenAI-API-compatible runtime — the same RAG
pattern the live system uses, reduced to a portable recipe you can replay on a
fresh box, a CI runner, or a remote node. Every path below targets an
OpenAI-API-compatible local endpoint per **Architectural Law 5
(UNIFIED-AI-REDIRECTS)**: no vendor-cloud URLs, no proprietary side-channels.
The single abstraction is `MIOS_AI_ENDPOINT` — point it at MiOS's own brain or
at any compatible runtime, and the recipes are identical.

---

## A) The canonical MiOS path — the local brain behind `MIOS_AI_ENDPOINT`

This is the endpoint MiOS itself uses. Two unit identities matter:

- **MiOS-Hermes** (`hermes-agent.service`, `/v1` on the `hermes` port) — the OpenAI-compatible
  *agent gateway* (sessions, tool-calling, skills, the browser/CDP tool loop).
  This is what `MIOS_AI_ENDPOINT` resolves to for agent-shaped traffic; the
  shipped example payload (`usr/share/mios/api/chat.local.example.json`) targets
  it directly.
- **MiOS-LLM-Light** (`mios-llm-light.service`, port key `llm_light`) — the **primary local
  inference lane**: `llama.cpp` behind the upstream `mios-llm-light` proxy image
  (`ghcr.io/mostlygeek/llama-swap`), with multi-model auto-swap and KV-cache
  paging. It serves the everyday models, the `mios-opencode` coder model, **and
  embeddings** (`nomic-embed-text`, OpenAI-compat `/v1/embeddings`). Config:
  `usr/share/mios/llamacpp/mios-llm-light.yaml`.

The KB ingestion only needs an OpenAI-compatible `/embeddings` surface (for
chunk vectors) and a `/chat/completions` surface (for queries and evals). On
MiOS, embeddings come from `mios-llm-light` and chat from the Hermes gateway —
both reachable through `MIOS_AI_ENDPOINT`.

```bash
# 0. Set the unified env vars (or rely on /etc/profile.d/mios-env.sh).
#    MIOS_AI_ENDPOINT is the single OpenAI-compat endpoint every MiOS agent/tool
#    targets (Architectural Law 5). The canonical default is the unified surface:
export MIOS_AI_ENDPOINT=${MIOS_AI_ENDPOINT:-http://localhost:${MIOS_PORTS_HERMES}/v1}
export MIOS_AI_KEY=${MIOS_AI_KEY:-}                          # empty key accepted locally
export MIOS_AI_MODEL=${MIOS_AI_MODEL:-mios-hermes}           # canonical mios.toml [ai] model
export MIOS_AI_EMBED_MODEL=${MIOS_AI_EMBED_MODEL:-nomic-embed-text}  # served by mios-llm-light on the llm_light port

# 1. Verify the endpoint
curl -fsS "$MIOS_AI_ENDPOINT/models" -H "Authorization: Bearer $MIOS_AI_KEY" | jq '.data[].id'

# 2. Confirm the unified agent datastore is up (PostgreSQL + pgvector is the
#    vector store — no separate vector DB to stand up). On a live MiOS host it
#    is the mios-pgvector container on :5432; the schema (knowledge, agent_memory,
#    event, session, skill, ... ) lives in usr/share/mios/postgres/schema-init.sql.
mios-db --pg -c "SELECT 1;"        # or: mios-pg-query "SELECT 1;"

# 3. Embed and ingest chunks.jsonl (universal RAG payload)
python3 ./var/lib/mios/embeddings/ingest_local.py    # emitted alongside chunks.jsonl
# That script reads chunks.jsonl, calls $MIOS_AI_ENDPOINT/embeddings,
# and upserts the vectors into the pgvector "knowledge" store.

# 4. Use it -- Chat Completions form (universal)
curl "$MIOS_AI_ENDPOINT/chat/completions" \
  -H "Authorization: Bearer $MIOS_AI_KEY" -H "Content-Type: application/json" \
  -d @./usr/share/mios/api/chat.local.example.json

# 5. Run the eval against the local endpoint
python3 ./var/lib/mios/evals/mios-knowledge.local-runner.py \
  --endpoint "$MIOS_AI_ENDPOINT" --model "$MIOS_AI_MODEL" \
  --eval ./var/lib/mios/evals/mios-knowledge.eval.json \
  --dataset ./var/lib/mios/evals/dataset.jsonl
```

The local-runner.py prints per-item pass/fail and a final pass rate; it
implements `string_check`, `text_similarity`, and `score_model` graders against
any `/v1/chat/completions` endpoint.

> If you want to talk to a specific lane directly rather than the unified
> surface: chat/agent traffic is Hermes on the `hermes` port, raw
> inference and embeddings are `mios-llm-light` on the `llm_light` port.
> Prefer `MIOS_AI_ENDPOINT` so the recipe stays portable.

---

## B) Other local runtimes (vLLM / LM Studio / llama.cpp / LiteLLM / Ollama-compat)

The whole point of the `MIOS_AI_ENDPOINT` abstraction is portability: the KB's
chat-form payloads and tool schemas are universal across every OpenAI-API-
compatible runtime. To ingest the KB elsewhere, change only the endpoint (and
model name) and re-run recipe A steps 3–5 unchanged:

```bash
# vLLM (from `vllm serve <model>` on default port) — same engine MiOS uses for
# its gated heavy lane (mios-llm-heavy)
export MIOS_AI_ENDPOINT=http://localhost:8000/v1
export MIOS_AI_MODEL=meta-llama/Llama-3.1-70B-Instruct

# LM Studio
export MIOS_AI_ENDPOINT=http://localhost:1234/v1
export MIOS_AI_MODEL=lmstudio-community/Qwen2.5-72B-Instruct-GGUF

# llama.cpp server (the same engine inside MiOS's mios-llm-light lane)
export MIOS_AI_ENDPOINT=http://localhost:8080/v1
export MIOS_AI_MODEL=any   # llama.cpp ignores model name, returns its loaded model

# LiteLLM proxy (translates many backends through one OpenAI-compatible surface)
export MIOS_AI_ENDPOINT=http://localhost:4000/v1
export MIOS_AI_MODEL=mios-hermes   # virtual model defined in litellm config.yaml

# Any Ollama-compatible runtime (MiOS speaks the Ollama-compatible API as an
# upstream interop standard; it does NOT run Ollama itself anymore)
export MIOS_AI_ENDPOINT=http://localhost:11434/v1
export MIOS_AI_MODEL=qwen2.5:32b   # or any served model with tool-calling support
```

Note: on a MiOS host, inference and embeddings are served by `mios-llm-light`
(`llama.cpp` via `mios-llm-light`), not by Ollama — Ollama, the legacy datastore, and Qdrant
have all been retired from the live stack. The Ollama-compatible API and the
upstream `mios-llm-light` image remain legitimate *interop* references, which is why
this recipe still works against any Ollama-compatible endpoint you point it at.

---

## Notes on tool calling with local models

Function calling support varies by model:

| Model family | Tool-call support | Notes |
|---|---|---|
| Llama 3.1+ Instruct | [ok] | Native, OpenAI-format tools |
| Llama 3.2 Vision | [ok] | Same |
| Qwen 2.5 / 3 Instruct | [ok] | Excellent; supports parallel tool calls |
| Gemma (instruct) | [ok] | Used by MiOS as a reasoning lane |
| Mistral / Mixtral Instruct | [ok] | Recent versions |
| Hermes 3 / Hermes 4 | [ok] | Specifically tuned for tools |
| Firefunction v2 | [ok] | Purpose-built for function calling |
| Phi-3+ Instruct | [!] partial | Single tool calls only |
| Older base models |  | Use a tool-tuned variant |

For models that ignore `strict: true`, ship the schemas anyway — they serve as
in-context documentation for the model. If you need *enforced* JSON Schema
compliance locally, use **vLLM with xgrammar** or **llama.cpp grammars** (compile
your JSON Schema to a GBNF grammar). The latter is the same engine MiOS runs in
`mios-llm-light`, so grammar-enforced output is available on the primary lane.

---

## Cloud-only OpenAI API surfaces

The OpenAI API spec defines surfaces that local OpenAI-compatible runtimes
(llama.cpp/`mios-llm-light`, vLLM, LM Studio, Ollama-compatible servers)
typically do **not** implement: Vector Stores, Files
(`purpose=assistants`/`fine-tune`), Batch API, Evals API, Fine-tuning Jobs.
**MiOS does not document a recipe that hardcodes a vendor-cloud endpoint** —
Architectural Law 5 forbids it. This is by design: MiOS provides those
capabilities locally instead (pgvector replaces Vector Stores for RAG; the eval
runner in recipe A replaces the Evals API; see below for fine-tuning).

If you have an OpenAI-API-compatible endpoint that *does* implement these
surfaces (a LiteLLM proxy fronting a custom backend, a self-hosted compatible
service), set `$MIOS_AI_ENDPOINT` and `$MIOS_AI_KEY` and the recipes work
unchanged. For local fine-tuning without those surfaces, see
`usr/share/mios/cookbooks/finetune-flow.md` (axolotl / trl / MLX-LM / unsloth
paths).

---

## Refreshing the KB from upstream 'MiOS'

The KB describes the live system, so it should be regenerated whenever the image
changes (a `bootc upgrade` carries forward a new build of `usr/`, `etc/`, `srv/`,
`var/` — and with it the docs the KB is built from):

```bash
# Use the mios_build_kb_refresh function tool against any local endpoint.
# Or manually:
git clone https://github.com/mios-dev/MiOS.git /tmp/mios-src
python3 ./tools/regenerate_chunks.py /tmp/mios-src ./var/lib/mios/embeddings/chunks.jsonl
# then re-run the ingestion in recipe A (steps 3-5) to re-embed into pgvector.
```
