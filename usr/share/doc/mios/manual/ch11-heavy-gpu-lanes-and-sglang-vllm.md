<!-- AI-hint: Chapter 11: The Heavy GPU Lane and its Engines (vLLM or SGLang). One heavy dGPU lane, mios-llm-heavy, whose SSOT spec carries the engine choice ([ai].heavy_engine) and renders the matching Quadlet. Documents both engines' options, the one port/node/route, and VRAM gating, LoRA and the CPU fallback. -->

# Chapter 11: The Heavy GPU Lane and its Engines (vLLM or SGLang)

> Part IV: Detailed Inference & Execution Layers of the [MiOS manual](../manual.md).

MiOS has **one** heavy dGPU inference lane, `mios-llm-heavy`. It runs either
vLLM or SGLang. The engine is a setting of that lane, chosen in the SSOT, so
there is no second heavy unit, port, node or route to keep in step.

### <a name="11_one_heavy_lane_two_engines"></a>11.One Heavy Lane, Two Engines: One Heavy Lane, Two Engines

> Path Reference: `/usr/share/doc/mios/manual.md#11_one_heavy_lane_two_engines`

#### Overview

`[ai].heavy_engine` in `mios.toml` selects the engine: `"vllm"` (the vendor
default) or `"sglang"`. The operator edits it in `mios.html`, where it is the one
heavy-lane control shown by default (Personal zone, "Heavy AI lane").

The lane's spec, `[containers.mios-llm-heavy]`, has two layers:

- An **engine-neutral base**: the GPU device, the shared weights volume
  `/var/lib/mios/vllm/model`, the `mios-ai.pod` membership, the gate
  `ConditionPathExists=/var/lib/mios/vllm/model/config.json`, the environment
  files (including the GPU NUMA affinity in `/run/mios/gpu-numa.env`), and labels
  that point at the lane's one port.
- One **overlay per engine**, `[containers.mios-llm-heavy.engine.<engine>]`, with
  that engine's `Image`, `Exec`, `Environment` additions and `Description`.
  `[containers.mios-llm-heavy.engine].select = "ai.heavy_engine"` names the key
  that picks the overlay.

#### How the Quadlet is rendered

`mios-gen pod-quadlets` (run by `mios-gen sync` and at image build by
`automation/33-generate-quadlets.sh`) merges the selected overlay onto the base:
a list adds to the base list (duplicates dropped) and a scalar replaces the base
value. The `engine` table itself is never rendered. The generated
`usr/share/containers/systemd/mios-llm-heavy.container` names its engine in its
header (`# Engine: vllm (selected by [ai].heavy_engine; ...)`). The pull unit
`mios-llm-heavy.image` follows the same choice through
`[images.mios-llm-heavy.engine]`.

The generator fails the projection when the selector is missing, is not a
string, or names an engine with no overlay, and lists the engines that exist.
`check_converge_ssot` checks the same thing on the SSOT and also fails if no
spec reads `[ai].heavy_engine`.

#### One port, one node, one route

- **Port**: both engines bind the port key `llm_heavy`
  (`${MIOS_PORTS_LLM_HEAVY}`), allocated in `[ports.categories.inference]`. The
  empty slot after it is the retired second heavy lane, kept empty so the iGPU
  lanes keep their ports.
- **Node**: `[nodes.local-heavy]` points at that port and advertises
  `mios-heavy`, the `served_name` both engines use. It is health-gated, so it
  joins the fan-out only while the lane answers.
- **Route**: the agent-pipe lane resolver has one heavy lane (`heavy`) in front
  of the always-on `light` floor. `[ai].heavy_engine` never adds a lane. The env
  override `MIOS_AGENT_PIPE_HEAVY_ENGINE=light` still routes around the heavy
  lane, and an explicit comma order (`heavy,light`) still works; an engine name
  inside that list means the heavy lane.

Switching engines is one SSOT edit: set `[ai].heavy_engine`, then rebuild (or
re-run `mios-gen pod-quadlets`) and restart `mios-llm-heavy.service`. Both
engines' images stay in `[build.bake].core`; `mios-bake-plan` pulls only the
selected engine's image and accepts the other as an unselected overlay image.

#### System References

- Relevant configurations: `mios.toml` `[ai].heavy_engine`,
  `[containers.mios-llm-heavy]`, `[images.mios-llm-heavy]`,
  `[nodes.local-heavy]`, `[ports].llm_heavy`
- Generator: `tools/native/mios-gen/src/pod_quadlets.rs` (engine overlays)
- Runtime services: `mios-llm-heavy.service` (gated, off by default),
  ``MIOS_AI_ENDPOINT``

---

### <a name="11_engine_options"></a>11.Engine Options: Engine Options (vLLM and SGLang)

> Path Reference: `/usr/share/doc/mios/manual.md#11_engine_options`

#### Overview

Each engine keeps all of its options. They live in the engine's own table and in
`mios.html` under Advanced settings, AI endpoints & models. The overlay `Exec`
reads them through `${MIOS_VLLM_*}` / `${MIOS_SGLANG_*}`, which `mios-gen`
resolves from the SSOT when it renders the unit.

| Engine | Options table | Strength | Surface settings |
|---|---|---|---|
| vLLM (`docker.io/vllm/vllm-openai`) | `[ai.vllm]`: `served_name`, `gpu_util`, `max_model_len`, `kv_cache_dtype`, `quantization`, `tool_call_parser`, `prefix_caching`, `v1_engine` | throughput at moderate context: PagedAttention, automatic prefix caching across the shared swarm prompt, runtime multi-LoRA | `[lanes.vllm]` |
| SGLang (`docker.io/lmsysorg/sglang`) | `[ai.sglang]`: `served_name`, `bake_model`, `mem_fraction`, `tool_parser`, `reasoning_parser`, `kv_cache_dtype`, `hierarchical_cache`, `unified_radix_tree` | long context: HiCache spills inactive KV to CPU RAM, which with an fp8 KV cache lets the native 256k context fit a shared 24 GB card | `[lanes.sglang]` |

`[lanes.<engine>]` holds the agent-pipe's per-engine surface settings
(`stream_thinking`, `tool_call_parser`, `reasoning_parser`,
`constrained_tools`). The heavy lane uses the table of its selected engine;
`check_structured` keeps both tables present because either can be selected.

#### Choosing an engine

- Pick **vLLM** for throughput at moderate context, and for runtime LoRA
  adapters (see the next section). Its fp8 KV budget on a shared 24 GB card
  admits well short of a full 256k sequence.
- Pick **SGLang** when turns need the full 256k context. HiCache plus
  `kv_cache_dtype = "fp8_e5m2"` is what makes it fit.

Both serve the same weights from `/var/lib/mios/vllm/model`, so switching
engines does not re-download the model. Model selection for the lane is recorded
in `usr/share/doc/mios/reference/heavy-model-selection-2026-07.md`.

#### System References

- Relevant configurations: `mios.toml` `[ai.vllm]`, `[ai.sglang]`,
  `[lanes.vllm]`, `[lanes.sglang]`, `[image.sidecars].vllm` / `.sglang`
- Runtime services: `mios-llm-heavy.service`

---

### <a name="11_vram_allocation_and_scheduling"></a>11.VRAM Allocation and Scheduling: VRAM Allocation and Scheduling

> Path Reference: `/usr/share/doc/mios/manual.md#11_vram_allocation_and_scheduling`

#### Overview

The heavy lane is gated three ways, so it claims the dGPU only on purpose:

1. **Weights**: `ConditionPathExists=/var/lib/mios/vllm/model/config.json`. No
   baked or provisioned model, no start.
2. **Blade capability**: `[blade.requires].mios-llm-heavy` needs `gpu-serving`
   and `service-plane`. Only the `hybrid` and `compute` archetypes grant both,
   so a seat, controller or desktop blade never starts it.
3. **CPU fallback**: `[blade.cpu_fallbacks].mios-llm-heavy = "mios-llm-light"`.
   Where the lane cannot run, the same OpenAI `/v1` contract is served by the
   light lane.

#### One heavy process, one VRAM budget

There is one heavy process at a time. The earlier two-lane layout could load a
second heavy engine next to the first and overrun a 24 GB card; it no longer can.

| Lane | Typical VRAM |
|---|---|
| `mios-llm-light` (llama-swap, co-resident models) | ~6.7 GB |
| `mios-llm-heavy` (selected engine, shared base weights) | ~12 GB and up, per `gpu_util` / `mem_fraction` |

The engine's own fraction (`[ai.vllm].gpu_util` or `[ai.sglang].mem_fraction`)
is the knob that trades heavy-lane KV against what the light lane needs on a
shared card.

#### LoRA adapters (vLLM engine)

Runtime multi-LoRA is a vLLM feature, configured in `[converge.inference]`:
`vllm_lora_adapters_dir` (default `/var/lib/mios/lora-adapters/`) and
`vllm_allow_runtime_lora`. The agent-pipe endpoints
`/v1/inference/lora/load` and `/v1/inference/lora/list` work only while
`[ai].heavy_engine = "vllm"`, and `check_converge_ssot` fails
`vllm_allow_runtime_lora = true` under any other engine. Place adapter weights
in `coding/`, `reasoning/` or `vision/` subdirectories of the adapters directory
on the host.

#### System References

- Relevant configurations: `mios.toml` `[blade.requires]`,
  `[blade.cpu_fallbacks]`, `[converge.inference]`
- Runtime services: `mios-llm-heavy.service`, `mios-llm-light.service`
