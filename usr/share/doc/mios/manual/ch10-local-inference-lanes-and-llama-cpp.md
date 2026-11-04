<!-- AI-hint: Chapter 10: Local Inference Lanes and llama.cpp. Covers llama-swap model residency, explicit llama.cpp slot persistence and verified compute routing on the `llm_light` port. Maps GPU context management, prompt template bindings, and model formats. Documents model map configuration file and resource optimization strategies. -->

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
