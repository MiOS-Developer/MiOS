<!-- AI-hint: Chapter 10: Local Inference Lanes and llama.cpp. Covers llama-swap model residency, explicit llama.cpp slot persistence and verified compute routing on the `llm_light` port. Maps GPU context management, prompt template bindings, and model formats. Documents model map configuration file and resource optimization strategies, and the per-node model picks as host-tier fragments. -->

# Chapter 10: Local Inference Lanes and llama.cpp

> Part IV: Detailed Inference & Execution Layers of the [MiOS manual](../manual.md).

This chapter covers the documentation for **Local Inference Lanes and llama.cpp** under MiOS.

### <a name="10_llama_swap_proxy_architecture"></a>10.Llama Swap Proxy Architecture: Llama-Swap Proxy Architecture

> Path Reference: `/usr/share/doc/mios/manual.md#10_llama_swap_proxy_architecture`

#### Overview

The llama-swap proxy manages model requests on the `llm_light` port, serving as the single entry point for light inference tasks.

## Routing Logic
1. **Model Swap**: Swaps the underlying `llama-server` process on-demand to match the requested model name.
2. **Context Saving**: `--slot-save-path` supplies the directory for explicit llama.cpp slot save and restore operations. Setting the path alone does not save every inactive conversation or restore it after a model swap.
3. **KV Restoring**: A caller must request the supported slot restore action and handle incompatible or missing state. Do not report restoration from the presence of a directory alone.
4. **Residency**: The model map controls which models coexist. The shipped resident group has `swap: false`; its members can remain loaded together. Its `exclusive: true` allows a model in that group to unload other groups.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="10_embedded_inference_setup"></a>10.Embedded Inference Setup: Embedded Inference Setup

> Path Reference: `/usr/share/doc/mios/manual.md#10_embedded_inference_setup`

#### Overview

Embedded inference on MiOS uses optimized GGUF format weights to enable local execution on GPU or CPU.

## Setup Details
- **Context Size**: Standardized context boundaries are mapped dynamically in [73-model-prep.sh](automation/73-model-prep.sh).
- **Embeddings**: An embedding-configured llama-server runs in parallel to handle vector queries.
- **Safety**: Uses static model limits and resource controls to prevent container memory limit crashes.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="10_model_map_and_hot_swapping"></a>10.Model Map and Hot Swapping: Model Map and Hot Swapping

> Path Reference: `/usr/share/doc/mios/manual.md#10_model_map_and_hot_swapping`

#### Overview

Models are mapped in [llama-swap.yaml](../../../mios/llamacpp/llama-swap.yaml), defining served model aliases and parameters.

## Configuration
- **Model Keys**: Read the configured aliases in `/usr/share/mios/llamacpp/llama-swap.yaml` and the layered `mios.toml`. Do not infer an engine or GPU assignment from a model alias.
- **Auto-swap Gating**: The requested model and the configured group policy determine process startup and eviction. A healthy proxy does not prove that its requested model can complete inference.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

## Verified CPU and GPU routing

The public interface remains `MIOS_AI_ENDPOINT`. The implementation reuses
`/usr/lib/mios/agent-pipe/mios_pipe/routing/lanes_resolver.py` for automatic
primary-brain requests, including ordinary chat, refinement and native tool-loop
inference. It checks the configured heavy lane's availability and served model
before selection. Explicit endpoint choices, explicit raw model IDs and a
distinct configured micro model retain their configured destination.

The canonical GPU service is `mios-llm-heavy.service`. Its selected engine comes
from `[ai].heavy_engine`; the llama.cpp option can serve an already provisioned
GGUF without creating a second heavy-service identity. The CPU node and light
proxy retain separate roles. A CUDA-capable image name is not evidence that a
CPU node should own the GPU.

For collaborative inference, use independent CPU analysis and GPU drafting in
parallel when both results are useful. A critique of a draft depends on that
draft and must follow it. Give optional analysis a bounded contribution window;
cancel and await unfinished work when its deadline expires or the caller
disconnects. `asyncio.wait()` alone does not cancel pending tasks. Preserve useful
completed contributions and return a final response even when an optional peer
times out.

Verify execution at the actual selected endpoint and model. A list of council
members in a log proves selection only. Acceptance requires observed per-node
requests, useful results and the completed public response. Preserve caller
authorization and never forward one endpoint's credential to another endpoint.

## Upstream patterns and operational evidence

- [Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization):
  parallelize independent work, reduce unnecessary calls and stream useful output.
  These are orchestration patterns; they do not require a new agent framework.
- [Async task cancellation](https://docs.python.org/3/library/asyncio-task.html):
  explicitly cancel and await tasks left pending by a deadline. Use APIs supported
  by the installed Python runtime.
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/785322d17f2d51e5185cf996a1f4e46d87640841/tools/server/README.md):
  slot state and generation capacity are separate from process liveness. Upstream
  documents `/slots?fail_on_no_slot=1` for capacity checks; verify support in the
  pinned engine before adopting it. Keep the public model interface OpenAI-compatible.
- [llama-swap configuration](https://github.com/mostlygeek/llama-swap/blob/9fbc47ce96d10160e95994c7a26513c972201a74/docs/config.example.yaml):
  `swap` controls residency within a group; `exclusive` controls effects on
  other groups. Do not copy newer configuration fields into an older pinned image
  without validation.
- [API client retries and timeouts](https://github.com/openai/openai-python#retries):
  configure interactive timeout and retry policy explicitly. Automatic retries
  can repeat an agent request after a transport or server error. A request timeout
  is not a whole-agent wall-clock deadline, and partially completed actions must
  not be replayed as a recovery shortcut.

A runtime acceptance receipt should correlate a real public completion with the
selected engine process, model, elapsed time and GPU allocation. A container
marked healthy, a successful model-list request, or a loaded GPU process alone
does not establish that an interactive turn completed.

## Per-node model picks

The vendor tier does not choose a model per host. `[ai.host_thresholds]` picks a
served *name* for Hermes by dGPU VRAM class only, and every name resolves to the
GGUF a lane already loads. `[llamacpp.light_backends.<backend>].config` picks a model
map by backend, not by hardware class. So the per-node picks are host-tier
fragments. The vendor tier ships only what every host can use:

- One served entry per pick in `usr/share/mios/llamacpp/mios-llm-light.yaml`:
  `qwen3.5:4b-q4_K_M`, `lfm2.5:8b-a1b`, `granite4.2:3b`, `lfm3:1.7b`,
  `qwen3.6:35b-a3b` and `embeddinggemma2:270m`. Nemotron-3.5-Bolt-E4B is present but
  commented out. The chat entries use `--jinja` and symmetric q8_0 K/V, and the
  agent entries are served at 65536 tokens.
- An `[[ai.catalog]]` row per chat pick.
- `[llamacpp.light_cpu_tier].drop` removes the agent-class picks (3B and up) from
  the CPU-tier map. On a CPU-only host they run on `mios-cpu-node` through
  `cpu_node_model_file`.
- `[llamacpp].cpu_node_cache_type` replaces the cpu-node's literal q4_0 K/V. The
  vendor value stays q4_0 while any Blade's node still serves the dense 8B.

The served names that clients use (`mios-agent-cpu`, `mios-heavy`,
`nomic-embed-text` and every `granite4.1:8b` alias) do not change. A fragment changes
only the GGUF behind them.

### Verified GGUFs

These were checked against the Hugging Face model API and the GGUF metadata. The
architecture of each one is registered and has a model builder in mainline
llama.cpp at b12464, the build the CPU lane images run, and at b12602 (CUDA) and
b12816. The firstboot fetcher floats each `resolve/main` source to the current
commit and verifies that commit's LFS sha256, so the digests below are provenance,
not pins.

| Pick | Repository @ commit | File (bytes) | LFS sha256 | Licence | Arch |
|---|---|---|---|---|---|
| Qwen3.5-4B | `ggml-org/Qwen3.5-4B-GGUF` @ `8d43d974` | `Qwen3.5-4B-Q4_K_M.gguf` (3032091584) | `217002fa8008ef4fc6d548353a3aebef055a96e6ff8e0e2c0953ef8254a89c14` | Apache-2.0 | `qwen35` |
| LFM2.5-8B-A1B | `LiquidAI/LFM2.5-8B-A1B-GGUF` @ `49c14831` | `LFM2.5-8B-A1B-Q4_K_M.gguf` (5155564768) | `4923ec14f06b968b74d663e5949867d2d9c3bf13a20b8be1a9f9af39989b2bb0` | LFM Open License v1.0 | `lfm2moe` |
| Granite 4.2 3B | `ibm-granite/granite-4.2-3b-GGUF` @ `c40945d7` | `granite-4.2-3b-Q4_K_M.gguf` (2244011552) | `e0406663965846ae22a403456eb826ccce5f450840491f71952f18a7cb78e7d5` | Apache-2.0 | `granite` |
| LFM3-1.7B | `LiquidAI/LFM3-1.7B-GGUF` @ `cd0c74c5` | `LFM3-1.7B-Q4_K_M.gguf` (1099627808) | `3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4` | LFM Open License v1.0 | `lfm3` |
| EmbeddingGemma 2 (text) | `ggml-org/embeddinggemma-2-GGUF` @ `6ea3e3a3` | `embeddinggemma-2-Q8_0.gguf` (309855456) | `2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135` | Apache-2.0 | `gemma-embedding2` |
| Qwen3.6-35B-A3B | `unsloth/Qwen3.6-35B-A3B-GGUF` @ `a483e9e6` | `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` (22134528992) | `ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61` | Apache-2.0 | `qwen35moe` |
| Nemotron-3.5-Bolt-E4B (disabled) | `nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-4GB` @ `0e5d71fc` | `Nemotron-3.5-Bolt-E4B-Q4_K_M.gguf` (6180446208) | `c8af2b8aea10a690a1d9491db744a9446660173cb38fc489612b556615a69612` | OpenMDW-1.1 | `gemma4` |

Some picks have conditions before they can take a role:

- **LFM models.** LFM2.5 emits Pythonic tool calls, which llama.cpp's LFM2.5
  handler maps to OpenAI `tool_calls`. LFM3 emits Qwen3-Coder-style XML. Neither
  takes an agent or micro role until a JSON `tool_calls` smoke test through
  `--jinja` passes on that host. The test should cover a single call, parallel
  calls, `tool_choice=required` and a turn that needs no tool. The LFM Open License
  commercial terms have not been reviewed.
- **EmbeddingGemma 2.** It is a separate served name. Re-pointing
  `nomic-embed-text` at it changes the vector space, so that is a migration: bump
  `[pgvector].emb_version` and backfill.
- **Nemotron-3.5-Bolt-E4B.** It stays commented out until the PS5 GPU lane exists.
  The vendor's Q4_K_M is about 5.8 GiB, about 1.1 GiB more than the lane was sized
  on.

### Applying a fragment

1. Write the fragment for the node to `/etc/mios/mios.d/70-fleet-model-picks.toml`.
   It sorts after the existing host fragments, so its keys win within the host
   tier.
2. A host `[[ai.firstboot_models]]` list replaces the vendor list, because arrays do
   not merge. Each fragment therefore repeats `lfm2-700m.gguf`. Run
   `mios models sync` to fetch and verify the GGUFs, then `mios models list` to see
   them present.
3. The vendor Quadlets under `/usr` bake vendor values. Re-render the affected
   lane's unit from the layered SSOT the way this host applies host-tier changes,
   then restart that lane in a controlled window. Never stop a node as a fix.

### X570 host, MiOS-DEV (RTX 3060 Ti 8 GB, Ryzen 7 5800X, 24 GB VM)

The 3060 Ti serves nothing until the Windows host-RAM start gate clears. No model
choice changes that.

```toml
# /etc/mios/mios.d/70-fleet-model-picks.toml -- X570 MiOS-DEV
[llamacpp]
# Heavy lane (llama.cpp CUDA engine, per 50-mios-dev-heavy-gpu.toml): Qwen3.5-4B,
# 64k, symmetric q8_0. ~2.8 GiB weights + ~1.1 GiB KV keeps the 1024 MiB fit margin.
heavy_model_file = "Qwen3.5-4B-Q4_K_M.gguf"
heavy_context    = 65536
heavy_cache_type = "q8_0"
# CPU node: Granite 4.2 3B at 64k (~4.8 GiB) gives Hermes a 64k lane while the GPU
# is gated. LFM2.5-8B-A1B replaces it once its JSON tool_calls test passes.
cpu_node_model_file = "granite-4.2-3b-Q4_K_M.gguf"
# cpu_node_model_file = "LFM2.5-8B-A1B-Q4_K_M.gguf"
cpu_node_context    = 65536
cpu_node_cache_type = "q8_0"

# [ai]
# micro_model = "lfm3:1.7b"   # once its JSON tool_calls test passes; lfm2:700m until then

[[ai.firstboot_models]]
name   = "lfm2-700m.gguf"
source = "https://huggingface.co/LiquidAI/LFM2-700M-GGUF/resolve/main/LFM2-700M-Q4_K_M.gguf"

[[ai.firstboot_models]]
name   = "Qwen3.5-4B-Q4_K_M.gguf"
source = "https://huggingface.co/ggml-org/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"
lane   = "heavy"

[[ai.firstboot_models]]
name   = "granite-4.2-3b-Q4_K_M.gguf"
source = "https://huggingface.co/ibm-granite/granite-4.2-3b-GGUF/resolve/main/granite-4.2-3b-Q4_K_M.gguf"
lane   = "cpu-node"

[[ai.firstboot_models]]
name   = "LFM2.5-8B-A1B-Q4_K_M.gguf"
source = "https://huggingface.co/LiquidAI/LFM2.5-8B-A1B-GGUF/resolve/main/LFM2.5-8B-A1B-Q4_K_M.gguf"
lane   = "cpu-node"

[[ai.firstboot_models]]
name   = "LFM3-1.7B-Q4_K_M.gguf"
source = "https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF/resolve/main/LFM3-1.7B-Q4_K_M.gguf"
lane   = "light"
```

### B550 Blade (30.7 GiB RAM, Renoir-class Vega iGPU)

Apply this only after auditing memory. About 10.2 GiB was available at the last
probe, and Qwen3.6-35B-A3B needs about 21.5 GiB at 64k. The vendor MemoryHigh of 65%
(about 20 GiB) is too low for it, so the fragment raises it. Set
`cpu_node_threads` to the core count once the CPU is recorded.

```toml
# /etc/mios/mios.d/70-fleet-model-picks.toml -- B550 Blade
[llamacpp]
# CPU node: Qwen3.6-35B-A3B, coding agent and council chair, 64k, symmetric q8_0.
cpu_node_model_file  = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
cpu_node_context     = 65536
cpu_node_cache_type  = "q8_0"
cpu_node_memory_high = "75%"
cpu_node_memory_max  = "80%"

[[ai.firstboot_models]]
name   = "lfm2-700m.gguf"
source = "https://huggingface.co/LiquidAI/LFM2-700M-GGUF/resolve/main/LFM2-700M-Q4_K_M.gguf"

[[ai.firstboot_models]]
name   = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
source = "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
lane   = "cpu-node"
```

The iGPU's second lane (`granite4.2:3b` at 32k) is deferred. Setting
`light_backend = "vulkan"` switches the lane to the full model map. That map loads
`granite4.1:8b` locally, which will not fit beside the 21.5 GiB node. Run
`llama-bench` on the Vulkan image first, and decide how that map should be shaped.

### PS5 Blade (13.6 GiB shared GDDR6, Zen 2, gfx1013, USB-2 root disk)

Granite 4.2 3B at 32k with q8_0 KV needs about 3.4 GiB, against 8.6 GB for the dense
8B. That returns about 5 GB and takes the node out of swap. The 2.2 GB fetch writes
to the USB-2 root disk, whose stuck writes caused every recorded freeze. Stage it
while the Blade is idle, or after the replacement enclosure is installed.

```toml
# /etc/mios/mios.d/70-fleet-model-picks.toml -- PS5 Blade
[llamacpp]
cpu_node_model_file = "granite-4.2-3b-Q4_K_M.gguf"
cpu_node_context    = 32768
cpu_node_cache_type = "q8_0"

[[ai.firstboot_models]]
name   = "lfm2-700m.gguf"
source = "https://huggingface.co/LiquidAI/LFM2-700M-GGUF/resolve/main/LFM2-700M-Q4_K_M.gguf"

[[ai.firstboot_models]]
name   = "granite-4.2-3b-Q4_K_M.gguf"
source = "https://huggingface.co/ibm-granite/granite-4.2-3b-GGUF/resolve/main/granite-4.2-3b-Q4_K_M.gguf"
lane   = "cpu-node"
```

The GPU lane is deferred too. Nemotron-3.5-Bolt-E4B over Vulkan needs the
replacement disk, a `vulkaninfo` check and the same model-map decision as the B550.
