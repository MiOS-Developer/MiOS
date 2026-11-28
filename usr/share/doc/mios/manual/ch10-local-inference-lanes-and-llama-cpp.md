<!-- AI-hint: Chapter 10: Local Inference Lanes and llama.cpp. Covers llama-swap model residency, explicit llama.cpp slot persistence and verified compute routing on the `llm_light` port. Maps GPU context management, prompt template bindings, and model formats. Documents model map configuration file and resource optimization strategies, the tool-safe floor, the per-host model tiers each lane picks at start from the host's spec'd RAM, and the heavy engine detected per host. -->

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

## Model tiers

Every lane on every host serves tools. The model, its quantization and its context
scale with the host: bigger hosts get more precision, a bigger model or a longer
context, and smaller hosts get a smaller model. No lane ever goes below the
tool-safe floor. CPU tiers are sized from RAM and GPU tiers from VRAM. A host can
have either or both, and on an APU the two share one RAM pool.

### The tool-safe floor

`[llamacpp.tool_safe]` holds the rules, and `mios-gen tool-safe --check` (drift
check `check_tool_safe_floor`) fails the build when any lane KV key, tier row or
light model-map entry falls below them. `tests/test-speculative-decoding-config.py`
applies the same rule from Python.

| What | Floor |
|---|---|
| Chat and agent KV cache | symmetric `q8_0` or `f16`; never `q4_0`, `q4_1`, `q5_0`, `q5_1` or `iq4_nl` |
| Embedding KV cache | `f16` or `q8_0` |
| Dense model of 4B+ parameters, or any reasoning model | Q4_K_M or better, or the model's native format (gpt-oss MXFP4) |
| Model under 4B, or MoE with 2B or fewer active, non-reasoning | Q8_0 or better |
| Any lane that serves tools | never IQ1/IQ2/IQ3, Q2_K or Q3_K (not on the ladder) |

The facts each judgement uses (quant, total and active parameters, reasoning,
kind) are in `[llamacpp.gguf."<file>"]`, one row per GGUF.

### Where the tier is chosen

The heavy lane and `mios-cpu-node` serve one GGUF each. Each lane's `ExecStartPre`
runs `mios-gen model-tier --lane heavy|cpu-node --apply`, which:

1. measures the host: its spec'd RAM and, for the heavy lane, the GPU of the engine
   it resolves to;
2. takes the last row of `[[llamacpp.tiers.<lane>]]` that is enabled, has its
   GGUF on disk, has a `min_ram_mib` the host's RAM reaches, and has a `need_mib`
   within the lane's budget;
3. for the heavy lane, runs the heavy admission on that row: the GGUF, slots, and
   the exact CUDA device or the verified Vulkan render node;
4. writes the row to `/run/mios/model-tier/<lane>.env`
   (`[llamacpp.tier_policy].env_dir`), and for the heavy lane the admitted device,
   its group, llama.cpp's `--device` and the engine image.

The vendor heavy engine is `auto`: one llama.cpp unit that finds the host's GPU at
every start (`[llamacpp.heavy_auto]`). It takes the first kind in `prefer` the host
has:

| Host | Signal | Engine |
|---|---|---|
| Bare-metal NVIDIA | `/dev/nvidiactl` (the driver's control node) | `llamacpp` (CUDA) |
| WSL with an NVIDIA Windows driver | `/dev/dxg` and `/usr/lib/wsl/lib/libcuda.so.1` | `llamacpp` (CUDA) |
| AMD APU or card | an amdgpu render node (`[llamacpp.heavy_vulkan]`) | `llamacpp-vulkan` |
| None of these | | no heavy lane |

The unit's `ExecCondition` (`mios-gen model-tier --lane heavy --probe`) exits 1 when
the host has none, so systemd skips the unit instead of failing it, and the CPU
lanes keep serving. `/dev/dxg` alone is not enough: WSL exposes it for any vendor's
GPU. A host can still pin the engine in its own tier: `llamacpp` or
`llamacpp-vulkan` takes effect at the unit's next start, while `vllm` or `sglang`
need an image built with that engine.

On WSL the CUDA admission cannot use its bare-metal proof (a PCI-bound GPU and an
exact UUID CDI name), because WSL has neither. Whenever `[llamacpp.heavy_wsl]`'s
`device_node` exists, it takes the WSL proof instead, never as a fallback, and
fails closed on any mismatch:

- `/dev/dxg` is a character device whose major is the kernel's `misc` registration
  and whose number matches `/sys/class/misc/dxg/dev`;
- `lib/libcuda.so.1` resolves inside `/usr/lib/wsl` and is an ELF shared object;
- exactly one spec in `/run/cdi` named in `[gpu.cdi.nvidia].specs`
  (`wsl2-nvidia.yaml` from `mios-cdi-detect`, or `nvidia-wsl.yaml` from
  `nvidia-ctk --mode=wsl`) serves `[gpu.cdi.nvidia].device` with `/dev/dxg` and
  mounts the driver store;
- the device selects `all` (`[llamacpp.heavy_wsl].selector`), the one device a WSL
  spec serves. Only WSL accepts `all`; PCI still needs the UUID.

The heavy unit `Wants` and starts after `mios-cdi-detect.service`, which writes the
spec. These checks are ported from the light lane's verified dxg checks.

The unit reads that file after `install.env`, and the model, context and KV
references are `[generator].placeholders`, so systemd expands them at start. One
image therefore serves whatever each host's tier picks. The served names clients
use (`mios-heavy`, `mios-agent-cpu` and every `granite4.1:8b` alias) stay the same;
only the GGUF behind them changes.

When no row fits, the first row (the floor) still serves, so every node keeps tools.
When no row's GGUF is on disk yet, the lane serves its static `[llamacpp]` keys. A
failed pick stops the heavy lane, which fails closed, but never the cpu-node.

The light lane's helpers (micro and embeddings) are llama-swap map entries that
every host serves on demand. Their `micro` and `embed` tier rows are declared for
the floor check and sizing; a host does not swap them.

### Budgets

RAM is the RAM MiOS specs for the host, read as `MemTotal`: a bare-metal Blade's
installed RAM, or a WSL VM's `.wslconfig` `memory=`, which `Get-MiOS.ps1` and
`build-mios.ps1` render from `[bootstrap.dev_vm]` (`memory_mb`, else host RAM minus
`[bootstrap.dev_vm.host_reserve]`: the larger of `memory_gb` (8) and `memory_pct`
(50%)). It is never `MemAvailable`. A pick follows the spec, and the lanes' memory
bounds (`cpu_node_memory_high`/`_max`, the light lane's `memory_max`) keep it safe,
whatever happens to be free when the lane starts.

All values are in MiB and measured at each start:

| Lane | Budget |
|---|---|
| heavy, dGPU | VRAM from `nvidia-smi` (WSL's `/usr/lib/wsl/lib` copy included) minus `vram_reserve_mib` (1792) |
| heavy, APU | min(VRAM carve-out + GTT, `apu_gpu_ram_fraction` (0.40) x RAM), from amdgpu sysfs; an amdgpu device with at most `apu_max_vram_mib` (4096) of VRAM counts as an APU |
| cpu-node | min(`cpu_node_memory_high` of RAM, RAM - the CPU light lane's `memory_max` - `host_reserve_mib` (2560) - the APU GPU lane's need) |

Each row's `need_mib` is GGUF bytes, plus q8_0 KV at its context, plus recurrent or
sliding-window state, plus a compute allowance. The dense-8B row is calibrated on
the 8.6 GB measured at 32k with q4_0 KV on the 14G PS5, plus the q8_0 delta.

The cpu-node mixes model families with the heavy lane. Each GGUF's facts row carries
its `family`, the base-model lineage (a fine-tune keeps its base's). Among the rows
that fit, the cpu-node takes the last one whose family differs from the heavy lane's
pick. Failing that, it takes the last one that at least avoids the heavy lane's
GGUF, and failing that, the last one that fits. A pin overrides all of this.

### Tier tables

Heavy lane (CUDA `llamacpp` or Vulkan `llamacpp-vulkan` engine):

| Row | GGUF | Context | KV | Need (MiB) |
|---|---|---|---|---|
| `gpu-6g` | `Qwen3.5-4B-Q4_K_M.gguf` | 65536 | q8_0 | 4847 |
| `gpu-10g` | `Qwen3.5-4B-Q8_0.gguf` | 131072 | q8_0 | 7318 |
| `gpu-16g` | `gpt-oss-20b-MXFP4.gguf` | 65536 | q8_0 | 13185 |
| `gpu-24g` | `gpt-oss-20b-MXFP4.gguf` | 131072 | q8_0 | 14104 |

`mios-cpu-node`:

| Row | GGUF | Context | KV | Need (MiB) | Min RAM (MiB) |
|---|---|---|---|---|---|
| `ram-floor` | `LFM3-1.7B-Q8_0.gguf` | 32768 | q8_0 | 2273 | - |
| `ram-6g` | `granite-4.2-3b-Q8_0.gguf` | 32768 | q8_0 | 5482 | - |
| `ram-12g` | `granite-4.2-3b-Q8_0.gguf` | 65536 | q8_0 | 6945 | 12288 |
| `ram-16g` | `Qwen3.5-4B-Q8_0.gguf` | 131072 | q8_0 | 7318 | 14336 |
| `ram-20g-dense` | `granite-4.1-8b.gguf` (Q4_K_M) | 32768 | q8_0 | 9482 | 16384 |
| `ram-20g` | `LFM2.5-8B-A1B-Q8_0.gguf` | 65536 | q8_0 | 9514 | 16384 |
| `ram-32g` | `gpt-oss-20b-MXFP4.gguf` | 65536 | q8_0 | 13288 | 28672 |
| `ram-64g` | `Qwen3.6-35B-A3B-Q8_0.gguf` | 65536 | q8_0 | 36859 | 57344 |

`min_ram_mib` is compared with `MemTotal`, which sits below a host's nominal size (a
16 GB WSL VM reads about 15970 MiB), so a row for an N GiB class asks for about 7/8
of N GiB. The 8B rows therefore need more than a 16 GB spec.

Light-lane helpers: the micro lane (`[ai].micro_model`) is `lfm2-700m.gguf`
(LFM2-700M Q8_0), with `lfm3:1.7b` as the opt-in alternative. `mios-micro:1.5b`,
`mios-micro` and `micro` are Qwen2.5-Coder-1.5B-Instruct at Q8_0, 8k, resident: it
emits native OpenAI JSON tool calls, which LFM2's Pythonic format does not. The
embedders are EmbeddingGemma-300m QAT Q8_0 (`nomic-embed-text`) and EmbeddingGemma 2
Q8_0 (`embeddinggemma2:270m`). On a 14G Blade the CPU light lane's `memory_max` is
3482 MiB: `mios-micro` (about 2226) plus `lfm2:700m` (about 1165) fit at about 3391;
an embedding call on top makes the kernel reclaim the mmap'd GGUF page cache, which
is slower but kills nothing.

Two models need particular care. gpt-oss uses the Harmony parser, so clients must
send `reasoning_content` back on every tool-call turn. Qwen3.6-35B-A3B has 3B
active parameters, which is caution territory, so it serves only at Q8_0.

### Verified GGUFs

Checked against the Hugging Face model API. Every architecture has a model builder
in mainline llama.cpp at b12464 (the CPU images) and b12602 (CUDA). The fetcher
floats `resolve/main` to the current commit and verifies that commit's LFS sha256,
so the digests here are provenance, not pins.

| GGUF | Repository @ commit | Bytes | LFS sha256 | Licence | Arch |
|---|---|---|---|---|---|
| `Qwen3.5-4B-Q4_K_M.gguf` | `ggml-org/Qwen3.5-4B-GGUF` @ `8d43d974` | 3032091584 | `217002fa8008ef4fc6d548353a3aebef055a96e6ff8e0e2c0953ef8254a89c14` | Apache-2.0 | `qwen35` |
| `Qwen3.5-4B-Q8_0.gguf` | `ggml-org/Qwen3.5-4B-GGUF` @ `8d43d974` | 4482403264 | `1b19cef95f45262988c3a82319cc0018022ef4a930ee11182425023587cd2ca5` | Apache-2.0 | `qwen35` |
| `granite-4.2-3b-Q8_0.gguf` | `ibm-granite/granite-4.2-3b-GGUF` @ `c40945d7` | 3892651552 | `fbe986738041418e26de9e123ba740cb654931f85bf572a71bd01f9e6b85e53d` | Apache-2.0 | `granite` |
| `LFM3-1.7B-Q8_0.gguf` | `LiquidAI/LFM3-1.7B-GGUF` @ `cd0c74c5` | 1846238496 | `8f6b24182258205e9e01e21f1f24a1f208c1e8115578825d202ce39976af20b6` | LFM Open License v1.0 | `lfm3` |
| `LFM2.5-8B-A1B-Q8_0.gguf` | `LiquidAI/LFM2.5-8B-A1B-GGUF` @ `49c14831` | 9010195680 | `33ab3b8ce6a964fb8ebac89360c9b3cf72c4fa418d5e4c0a94d46883124d5c02` | LFM Open License v1.0 | `lfm2moe` |
| `lfm2-700m.gguf` (LFM2-700M-Q8_0) | `LiquidAI/LFM2-700M-GGUF` @ `fd39e80d` | 791565248 | `0967d902ed270d07cb374a1972bb5da4338d2a303b565cf4eb7948b958e751ab` | LFM Open License v1.0 | `lfm2` |
| `gpt-oss-20b-MXFP4.gguf` | `ggml-org/gpt-oss-20b-GGUF` @ `41e8a94d` | 12109566624 | `27cd6c432c7672cb812a92f611cf3ba7bbc35928262bb1e1253ff4ee6ae35901` | Apache-2.0 | `gpt-oss` |
| `Qwen3.6-35B-A3B-Q8_0.gguf` | `ggml-org/Qwen3.6-35B-A3B-GGUF` @ `52f9f9b8` | 36903139616 | `f189cedbe18dc524dd74014b23431081262bc9fcc57c18fec7a7a25149246570` | Apache-2.0 | `qwen35moe` |
| `embeddinggemma-2-Q8_0.gguf` | `ggml-org/embeddinggemma-2-GGUF` @ `6ea3e3a3` | 309855456 | `2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135` | Apache-2.0 | `gemma-embedding2` |
| `qwen2.5-coder-1.5b-instruct-q8_0.gguf` | `Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF` @ `f86cb2c1` | 1894532160 | `507de59046601282ba768a9789900e6ccf60ed93ddf346730b7c68eb0715bc47` | Apache-2.0 | `qwen2` |

Nemotron-3.5-Bolt-E4B (`nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-4GB`, Q4_K_M, 6180446208
bytes, OpenMDW-1.1) meets the floor for its class: 8B total, dense, so Q4_K_M. It
stays disabled because at about 7.1 GiB at 64k (6.8 GiB at 32k) it does not fit the
14G APU's GPU budget of about 5.4 GiB.

### Acceptance hosts

The arithmetic below is taken from the `light_tiers` tests:

| Host | Heavy lane | cpu-node |
|---|---|---|
| X570 MiOS-DEV at its MiOS spec: 8 GB RTX 3060 Ti, 16 GB VM (MemTotal about 15970) | 8192 - 1792 = 6400 → `gpu-6g`, Qwen3.5-4B Q4_K_M, 64k | min(10380, 15970 - 3992 - 2560) = 9418. `ram-16g` (Qwen3.5-4B Q8_0, 7318) fits but is Qwen like the heavy lane, so `ram-12g`, Granite 4.2 3B Q8_0, 64k (6945) |
| A 16 GB spec with no heavy lane | none | 9418 → `ram-16g`, Qwen3.5-4B Q8_0, 128k |
| X570 MiOS-DEV as it runs now: the VM hand-set to 24 GB | as above | min(15622, 24034 - 6008 - 2560) = 15466 → `ram-20g`, LFM2.5-8B-A1B Q8_0, 64k |
| PS5, CPU only (no Vulkan lane yet) | none | min(9055, 13931 - 3482 - 2560) = 7889 → `ram-12g`, Granite 4.2 3B Q8_0, 64k; never the dense 8B |
| PS5 APU: gfx1013, 1 GiB VRAM + 6.8 GiB GTT, 13.6 GiB | min(7989, 0.40 x 13931 = 5572) → `gpu-6g`, Qwen3.5-4B Q4_K_M, 64k on Vulkan | min(9055, 13931 - 3482 - 2560 - 4847) = 3042 → `ram-floor`, LFM3-1.7B Q8_0, 32k |
| B550: Renoir Vega APU, 30.7 GiB | min(16220, 12566) → `gpu-10g`, Qwen3.5-4B Q8_0, 128k on Vulkan | min(20420, 31416 - 7854 - 2560 - 7318) = 13684 → `ram-32g`, gpt-oss-20b, 64k |
| 64 GB CPU host | none | min(41795, 45665) → `ram-64g`, Qwen3.6-35B-A3B Q8_0, 64k |
| 24 GB dGPU host, 32 GB RAM | 24564 - 1792 = 22772 → `gpu-24g`, gpt-oss-20b, 128k | 20865 fits `ram-32g`, but that is gpt-oss like the heavy lane, so `ram-20g`, LFM2.5-8B-A1B |

The X570 is a 32 GB host, so MiOS specs its VM at `memory=16GB` (32 minus the larger
of 8 and 50% of 32). Its live `.wslconfig` says `memory=24GB`, set by hand, which
leaves Windows about 8 GB. Apply the spec on the Windows host: rerun `Get-MiOS.ps1`
(it reconciles `memory=`), or set `memory=16GB` under `[wsl2]` and run
`wsl --shutdown`. Until then the VM reads 24 GB and its cpu-node takes `ram-20g`.

On the PS5 APU everything stays inside 13.6 GiB with no swap: light lane at most
3482, GPU lane 4847, cpu-node 2273 and host reserve 2560 come to 13162 MiB, and no
GGUF is held twice. The B550 split assumes a 512 MiB carve-out and a default GTT of
half its RAM; the real values are read at start. Its last probe showed about 10.2
GiB available, so audit what holds the rest before relying on the full split.

### Downloads

Every host fetches every GGUF any tier or map entry can select. That is the
operator's choice: the vendor `[[ai.firstboot_models]]` list carries all of them,
about 74 GB on top of the baked set. A 14G console Blade writes all of it to its
USB-2 root, which is the same disk its freezes started on, so run its first fetch
(`mios models sync`) while it is idle. Until a lane's tier GGUF is on disk, the lane
serves its static keys. For the cpu-node those are the tool-safe floor, LFM3-1.7B
Q8_0 at 32k (about 2.2 GiB), which is baked into the image (`[llamacpp].bake_models`),
so every host, a fresh 14G Blade included, serves tools from first boot.

### Pinning and overriding a tier

A host decides in its own tier, for example `/etc/mios/mios.d/70-model-tier.toml`:

```toml
[llamacpp.tier_pin]
cpu_node = "ram-12g"       # pin a row by name; empty = automatic
heavy = ""

[llamacpp.tier_policy]
host_reserve_mib = 4096    # a VM that also runs the agent stack and builds keeps more back

[ai]
# The vendor "auto" detects the GPU at start; pin only to override it.
# heavy_engine = "llamacpp-vulkan"   # serve on the AMD render node
# heavy_engine = "llamacpp"          # serve on the NVIDIA card

[routing]
# The model router advertises router_ctx on every route. Raise it to 65536 only once
# every lane behind the Hermes routes serves 64k. On the X570 the light route still
# serves lfm2:700m at 32k, so it stays at 16384 there.
# router_ctx = 65536
```

A host can also replace a lane's whole `[[llamacpp.tiers.<lane>]]` table in its own
tier, because arrays do not merge; the floor check still applies to the vendor
rows. The pick is made at every start of the lane, and `mios-gen model-tier --lane
cpu-node` without `--apply` prints the plan and its arithmetic without writing.

### What still needs proving on the hosts

- No one has run Vulkan inference on the PS5's gfx1013 (`vulkaninfo` has not been
  run there) or on the B550's Vega. The Vulkan engine fails closed without an amdgpu
  render node.
- The `auto` unit relies on systemd expanding `${MIOS_LLAMACPP_HEAVY_*}` in the
  Quadlet's `Image=`, `AddDevice=` and `GroupAdd=` at start; no host has run it yet.
- The WSL CUDA admission has run only against fixtures shaped like MiOS-DEV's
  `/dev/dxg` (10:258, mode 666), its libcuda and its `wsl2-nvidia.yaml`. Its spec has
  been seen to disappear from `/run/cdi` after boot. `mios-cdi-detect` is a oneshot
  that stays active, so the unit's `Wants` runs it only once per boot. A lost spec
  makes the admission fail closed until `systemctl restart mios-cdi-detect`.
- The cpu-node plans as if the heavy lane serves whenever the host has its GPU. On
  MiOS-DEV, whose `headless` role keeps the heavy unit off today, it still mixes
  against the heavy lane's pick.
- The mios-micro OCI image still ships its Qwen2.5-Coder-1.5B Q4_K_M file under
  `/models-micro`, below the floor and no longer served. The mios-micro repository
  should publish the Q8_0, or the mount should go.
- Both Blades resolve to the vendor `hybrid` archetype, which grants
  `gpu-serving`. MiOS-DEV is demoted to `headless` on WSL by `usr/lib/mios/blade.sh`,
  so its heavy unit's `gpu-serving` gate needs the role work on its own branch.
- LFM2.5 and LFM3 tool calls go through llama.cpp's LFM2.5 and Qwen3-Coder parsers.
  Confirm them with a JSON `tool_calls` smoke test: single call, parallel calls,
  `tool_choice=required` and a turn that needs no tool. The LFM Open License
  commercial terms have not been reviewed.
