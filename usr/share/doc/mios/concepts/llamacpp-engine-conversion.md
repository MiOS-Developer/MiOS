<!-- AI-hint: Records MiOS's completed inference-engine conversion to llama.cpp (via the upstream llama-swap proxy image) to unlock fleet-wide KV-cache checkpoint/restore/fork for the AIOS Context Manager; Ollama is retired, mios-llm-light (port key `llm_light`) is the primary lane and also serves embeddings.
     AI-related: /usr/share/mios/llamacpp/models/.ready, /usr/share/mios/llamacpp/models/, /usr/share/mios/llamacpp/mios-llm-light.yaml, mios-llm-light, mios-llm-heavy, mios-llm-heavy-alt, mios-llm-worker, mios-agent-pipe, mios-llm-light.container -->
# Inference-engine conversion to llama.cpp for KV-cache (WS-10)

> Status: LANDED. The conversion this doc planned has shipped — MiOS now runs
> all everyday inference on **llama.cpp behind the upstream llama-swap proxy** as
> `mios-llm-light` (port key `llm_light`), and **Ollama is fully retired** (containers,
> firstboot, model-bake, Modelfiles, CLI shim — all removed). The body below is
> preserved as the design record and rationale; the per-lane table and the
> build/cutover notes are updated to current state. Companion:
> `postgres-pgvector-unification.md` (the parallel datastore conversion to
> PostgreSQL + pgvector).
>
> Audience: engineers extending the MiOS image and its AI plane. Purpose: explain
> *why* MiOS inference is llama.cpp + the upstream llama-swap proxy, *what* that
> buys the system (fleet-wide KV-cache), and *how* the lanes and the agent-pipe
> fit together.

## 0. Where this sits in the whole system

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is a single container image you `bootc
upgrade` like a `git pull` and `bootc rollback` like a Ctrl-Z) that is *also* a
**local, self-replicating, agentic AI operating system** — a full agent stack
behind one OpenAI-compatible endpoint, baked into that same image.

End to end: a request enters from a front-end (OWUI, port key `open_webui`, the Discord gateway,
the `mios` CLI) into the **agent-pipe** orchestrator (port key `agent_pipe`), which refines it,
fans it out across a council/swarm, and dispatches tool/verb calls; **MiOS-Hermes**
(port key `hermes`) is the OpenAI-compatible gateway and tool-loop agent; **PostgreSQL +
pgvector** (`mios-pgvector`, :5432) is the unified agent memory (tiered memory,
knowledge, sessions, skills, RAG embeddings); **MCP** exposes the tool surface
and **A2A** federates peer agents. None of that does any token generation itself
— it all bottoms out in the **inference lanes**, which are the subject of this
doc. The lanes are what actually run the models and the embeddings, and the
*engine* those lanes use is what decides whether MiOS can do context-paging.

This conversion is the piece that lets the agent plane keep a conversation's
working memory on disk (checkpoint / restore / fork) rather than throwing it away
on every model swap — the AIOS Context Manager. Picking the right engine here is
what makes that primitive possible fleet-wide instead of on one lane.

## 1. Why — KV-cache is the whole point

The AIOS Context Manager (gap #3) needs `kv_checkpoint` / `kv_restore` /
`kv_fork`. Engine reality (from the engine-strategy audit):

- **Ollama = ✗** no KV save/restore at all. Only `keep_alive` residency
  (VRAM→RAM→evict); a swap *unloads*, never checkpoints. Ollama can never reach
  context-paging. (Ollama is now removed from MiOS entirely; it survives only as
  an *upstream API-compat reference* — the engines still speak the
  OpenAI/Ollama-compatible API.)
- **llama.cpp = ✓** real, usable today: `--slot-save-path` + `POST
  /slots/{id}?action=save|restore` writes a conversation's KV to disk and
  restores near-instantly. **Already proven in MiOS** on the iGPU lane:
  `_kv_paging` (server.py) demand-pages the OpenAI `/v1/chat/completions` path —
  measured `n_saved:62` round-trip with `--parallel 1` (one slot, deterministic
  routing).

Converting every lane to llama.cpp **generalizes `_kv_paging` from one node to
the whole fleet** + enables `kv_fork` (slot copy → parallel cognitive paths for
the swarm). That is the operator's "compress / write-to-disk / clean state on
load-unload" requirement, fleet-wide.

## 2. The linchpin — the upstream llama-swap proxy (restores multi-model)

Ollama's killer feature was the multi-model daemon (auto-load, LRU evict, hot
swap). llama.cpp is one-model-per-process. The **upstream llama-swap proxy**
(FOSS, MIT; `ghcr.io/mostlygeek/llama-swap`) is the standard fix: a small proxy
that, per requested `model`, launches/swaps the right `llama-server` on demand
and exposes ONE OpenAI `/v1` endpoint per lane. It gives back Ollama's "many
models, auto-loaded" UX while every server underneath is llama.cpp (so KV-paging
works). Config = a YAML mapping `model → gguf path + server flags (--ctx-size,
--n-gpu-layers, --parallel 1, --slot-save-path)` at
`usr/share/mios/llamacpp/mios-llm-light.yaml`.

This replaced Ollama's `/api/ps` residency subsystem with the upstream
llama-swap proxy's process orchestration (its `/running` + swap), which
`_admit`/`_reclaim_idle_vram` query instead of `/api/ps`.

The upstream llama-swap proxy (the tool/image) and the Ollama-compatible API are
legitimate upstream references and remain in the design. Only the MiOS *unit /
service identity* is named: the lane ships as **`mios-llm-light`**.

## 3. Per-lane architecture (current)

| Lane | Engine / unit | Role |
|---|---|---|
| **Light / primary** (port key `llm_light`) | `mios-llm-light` — mios-llm-light → `llama-server` per model (CUDA via CDI) | Everyday chat/reasoning (router/refine/polish/planner/judge/council), the `mios-opencode` coder model, **and embeddings**. KV-pageable (`--parallel 1 --slot-save-path`). This is the SSOT inference backend. |
| **Embeddings** | same `mios-llm-light` lane, `nomic-embed-text` GGUF via `llama-server --embedding` | OpenAI `/v1/embeddings` (768-dim) — feeds pgvector RAG, knowledge, memory. (Replaced the Ollama embed lane.) |
| **Heavy / throughput** (port key `vllm`) | `mios-llm-heavy` — vLLM, served-name `mios-heavy` | Gated/off-by-default (VRAM). PagedAttention + Automatic Prefix Caching (APC) for concurrent swarm fan-out. The throughput tier. |
| **Heavy-alt** (port key `sglang`) | `mios-llm-heavy-alt` — SGLang, served-name `mios-heavy` | Gated/off-by-default. Continuous batching + HiCache CPU KV-offload. Mutually exclusive with `mios-llm-heavy` on a shared GPU (both serve `mios-heavy`). |
| **Swarm workers** | `mios-llm-worker@` (templated) | Single-model swarm workers for the dGPU swarm topology. |

The heavy lanes are **not either/or with llama.cpp** — they are a different tier.
llama.cpp KV-paging is the *per-conversation* tier (one resident slot, paged to
disk on conversation switch); SGLang/vLLM are the *high-concurrency throughput*
tier (continuous batching / PagedAttention + APC) for broad fan-outs. Both are
gated and VRAM-admission-governed (`health_gate=true` → a lane auto-joins the
swarm only when actually reachable).

KV-cache primitives (server.py): the existing `_kv_paging` generalizes from the
old `kv_paging_hints="11436"` gate to **every llama.cpp endpoint** (it keys off
`_endpoint_is_llamacpp`, which is config-first: `api="llamacpp"` → `_kv_paging`
fires on ANY endpoint, no port hardcode — server.py:2249). `kv_fork` =
`/slots/{src}?action=save` → restore into a new slot/conversation for parallel
branches.

## 4. Conversion blast radius (the seam that made it cheap)

The OpenAI `/v1` seam already existed and is engine-agnostic (Law 5 —
UNIFIED-AI-REDIRECTS); `_endpoint_is_ollama`/`_endpoint_is_llamacpp`/
`_binding_api`/`_endpoint_supports_tool_choice` are config-first `api=` switches,
llama.cpp was already a recognized `api` value, and `_kv_paging` already worked.
So the conversion was mostly **routing the lanes through the existing `/v1` path
instead of the Ollama `/api/chat` path**, plus model orchestration. The audited
touch-points (now migrated):

- **~13 Ollama-native `/api/chat` call sites** (refine, polish, router, planner,
  judge, knowledge, fan-out ×2, tool-loop; + mios-daemon; + ~4 OWUI-pipe sites)
  carried Ollama-only fields (`think`, `keep_alive`, `format:json`,
  `options.{num_ctx,num_gpu,num_thread}`) and **NDJSON** streaming (≠ OpenAI
  SSE). → routed via the `/v1` chat path; per-request options mapped to
  `llama-server` launch flags (the mios-llm-light config).
- **`/api/ps` VRAM residency subsystem** (`_ollama_resident`/`_vram_checkpoint`/
  `_ollama_unload`/`_reclaim_idle_vram`/`_is_warm`) → mios-llm-light `/running` +
  swap (process orchestration; one-model-per-proc).
- **`_embed_one` `/api/embeddings`** → `/v1/embeddings` on the `mios-llm-light`
  embedding model (`nomic-embed-text`).
- **Hardcoded Ollama-port detections** → routed through `_endpoint_is_ollama`
  (SSOT), since those ports became llama.cpp.
- **`hermes_backend_url` / lane `endpoint`s** (mios.toml) → repointed off the
  retired Ollama ports (`:11434`/`:11435`, removed at G5) to the `llm_light`
  (light) / `vllm` (heavy) ports.
- **Model provisioning**: `ollama create` (Modelfile) + the model-bake step were
  replaced by **baked `.gguf` files** + the mios-llm-light model map. The Modelfile
  `SYSTEM` prompt was portable (the runtime contract overrides it); `num_ctx` /
  `num_gpu` became launch flags.

## 5. Quadlets / SSOT (current)

- **`mios-llm-light.container`** — the mios-llm-light quadlet (host-net, CUDA via
  CDI `nvidia.com/gpu=all`, config + models + slot-save mounts). Runs as the
  `mios-llamacpp` service identity (uid/gid **827**, numeric — the upstream image
  has no `mios` user); pinned in `sysusers.d`. Bound-image (Law 3),
  `Delegate=yes` (Law 6). Inert until models exist via
  `ConditionPathExists(/usr/share/mios/llamacpp/models/.ready)` so a missing-GGUF
  start can't crash-loop. (A WSL2 dGPU fix prepends `/usr/lib/wsl/lib` +
  `/usr/local/cuda/lib64` to `LD_LIBRARY_PATH` so `llama-server` finds the WSL
  `libcuda` and offloads to the 4090 instead of running on CPU.)
- **`mios-llm-heavy.container`** (vLLM, port key `vllm`) and
  **`mios-llm-heavy-alt.container`** (SGLang, port key `sglang`) — the gated heavy tiers,
  weights baked opt-in, off by default behind a weights `ConditionPathExists`.
- **`mios.toml`**:
  - `[llamacpp]` — `enable=false` (additive + gated until GGUFs are baked and
    verified), `slot_dir=/var/lib/mios/llamacpp/slots`,
    `models_dir=/usr/share/mios/llamacpp/models`,
    `config=/usr/share/mios/llamacpp/mios-llm-light.yaml`, `bake_models` (the GGUF
    bake CSV; `38-llamacpp-prep.sh`).
  - `[ports].llm_light`; `[image.sidecars]` →
    `ghcr.io/mostlygeek/llama-swap:cuda` (`llm_light_version="cuda"`).
  - `[services.llamacpp]` → uid/gid 827.
  - Lane nodes (`[nodes.*]`) and agent endpoints point at the resolved endpoints;
    `api="llamacpp"` on a node turns on `/slots` KV-paging and disables
    `tool_choice=required` for that lane.

## 6. Risks (honest) and the mitigations carried forward

- **Swap cold-start latency** — mios-llm-light loads a model per swap (no warm
  multi-model pool). Mitigated by pinning the hot models (refine/polish/router)
  to always-resident servers and swapping only the long tail; the existing
  admission + lane caps already serialize cold loads.
- **Tool-calling robustness** — llama.cpp function-calling uses `--jinja` chat
  templates + grammar, historically finickier than Ollama (the per-lane tool-cap
  and the no-`tool_choice` hints on llama.cpp lanes exist because
  grammar-constraining all ~71 verbs times out). Those guards carry forward;
  validate the function-call path per model before relying on it. (Cf. the
  parallel finding on the SGLang heavy lane, where `--tool-call-parser qwen25` is
  required or the Qwen `<tool_call>` block leaks as plain content and never
  executes.)
- **One-model-per-process VRAM** on the shared 4090 — fewer concurrent models
  than Ollama's pool. The gated heavy lanes + KV-paging offset this; lane
  concurrency is capped low (`lane_concurrency_gpu`) so excess nodes queue rather
  than OOM.

## 7. Staged plan (as executed)

1. **Embeddings first** (lowest risk): `llama-server --embedding` (`nomic-embed-text`
   GGUF); `_embed_one` + OWUI RAG flipped to `/v1/embeddings`; 768-dim parity.
2. **mios-llm-light on the light lane** (port key `llm_light`): the everyday models; verified the
   `/v1` path + tool-calls + KV-paging.
3. **Generalize `_kv_paging`** to all llama.cpp endpoints; add `kv_fork`.
4. **Migrate the ~13 call sites** to `/v1`; replace the `/api/ps` subsystem with
   mios-llm-light `/running`.
5. **Model provisioning** → `.gguf` bake + mios-llm-light map; retire Modelfiles /
   `ollama create`.
6. **Retire Ollama** once parity (chat + tools + embeddings + KV) held — done at
   G5: all Ollama containers, firstboot, model-bake, Modelfiles, and the CLI shim
   are removed.
7. The heavy lanes (`mios-llm-heavy` vLLM, `mios-llm-heavy-alt` SGLang) stay the
   gated throughput tier, VRAM-admission-governed.

Net: KV checkpoint/restore/fork fleet-wide (the AIOS Context Manager realized),
all on a FOSS (MIT) engine, with mios-llm-light preserving multi-model ergonomics —
and that engine choice is what lets the rest of the system (agent-pipe → Hermes →
pgvector memory → MCP/A2A) keep a conversation's working state on disk instead of
discarding it on every swap.

---

## Build history — additive engine step (2026-06-04): mios-llm-light infra DONE (then cut over)

> Historical record of how the lane was stood up additively before Ollama was
> retired. Preserved for rationale; the cutover (step 4 below) has since landed.

Stood up the llama.cpp multi-model lane ALONGSIDE Ollama, GATED OFF until GGUFs
were provisioned (nothing live moved at the time). **KEY FINDING:** the
agent-pipe was ALREADY ready for the swap — `_endpoint_is_llamacpp`
(server.py:2249) is config-first (`api="llamacpp"` → `_kv_paging` fires on ANY
endpoint, no port hardcode) and `/v1` is engine-agnostic (Law 5). So WS-10 was
**infra + GGUF provisioning**, not a server.py rewrite.

New / edited at the time:
- `usr/share/containers/systemd/mios-llm-light.container` — the mios-llm-light quadlet
  (host-net, CUDA CDI, config + models + slot-save mounts, uid 827). Inert via
  `ConditionPathExists(/usr/share/mios/llamacpp/models/.ready)`.
- `usr/share/mios/llamacpp/mios-llm-light.yaml` — the model map: chat/reasoning models
  with `--parallel 1 --slot-save-path` (KV-pageable) + `nomic-embed-text`
  `--embedding` (replaces the Ollama embed lane). (Live finding: the custom
  `qwen35` arch failed to load on mainline llama.cpp — `gemma4:12b` (standard
  gemma arch) became the wired reasoning model; legacy/role model names are
  aliased onto it so the pipeline resolves to one served GGUF until SSOT
  reconciliation.)
- SSOT: `[ports].llm_light`, `[image.sidecars].llm_light`
  (`ghcr.io/mostlygeek/llama-swap:cuda`), `[services.llamacpp]` (uid 827),
  `[llamacpp]` (enable=false, slot_dir/models_dir/config).
- Identity/wiring: sysusers (`mios-llamacpp` 827 + `mios-ai`), tmpfiles (slot
  dir), userenv (`llamacpp.*` / llm_light maps), 15-render allow-list.

Cutover steps (since completed):
1. **GGUF provisioning** — offline GGUF bake (`38-llamacpp-prep.sh`) →
   `/usr/share/mios/llamacpp/models/*.gguf` + `touch .ready`.
2. **Verify the template live** — mios-llm-light image tag, the `cmd`/`proxy`/`ttl`
   schema, the `llama-server` binary path inside the image (`/app/llama-server`),
   `--config`/`--listen` flags, and `--n-gpu-layers` sizing for the shared 4090.
3. **Wire the lane** — `[nodes.*]` at `http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1`,
   `api="llamacpp"` → auto-joins the swarm + KV-pages, zero pipe changes.
4. **Cutover** — point `_embed_one` at the mios-llm-light embed endpoint; migrate the
   chat lanes off Ollama; retire Ollama. (Done — Ollama is fully removed.)
