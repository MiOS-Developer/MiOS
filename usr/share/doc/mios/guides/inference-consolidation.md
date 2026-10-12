<!-- AI-hint: MiOS architectural documentation: Inference consolidation (WS-CONV-07).
     AI-related: mios-llm-heavy, mios-llm-heavy-alt, mios-llm-light, mios-sync-env, mios-llm-heavy.service, mios-llm-heavy-alt.service -->

# Inference consolidation (WS-CONV-07)

This guide documents the consolidation of heavy inference engines under the converged-resource architecture.

## Dual-Heavy Topology vs. Single-Engine vLLM Multi-LoRA

The default MiOS deployment runs a dual-heavy engine topology:
1. `mios-llm-heavy` serving the primary heavy text/reasoning engine.
2. `mios-llm-heavy-alt` serving a secondary heavy text/reasoning engine.

This dual-heavy engine configuration exceeds the VRAM limits of a single RTX GPU, causing memory thrashing and slow downs when both models load simultaneously.

By moving to a unified `single` engine mode with vLLM multi-LoRA support, the primary `mios-llm-heavy` process dynamically loads task-specific LoRA adapters (e.g. coding, reasoning) on top of a shared base model. This allows us to retire the `mios-llm-heavy-alt` process, saving massive amounts of VRAM.

## VRAM Budget After Consolidation

| Before | VRAM | After | VRAM |
|---|---|---|---|
| `mios-llm-light` llama-swap (3 models co-resident) | ~6.7 GB | `mios-llm-light` (unchanged, + cache-reuse) | ~6.7 GB |
| `mios-llm-heavy` (vLLM, separate process) | ~12 GB | `mios-llm-heavy` (vLLM, multi-LoRA, shared base) | ~12 GB |
| `mios-llm-heavy-alt` (:11440, second process) | ~12 GB | ~~`mios-llm-heavy-alt`~~ (retired) | **0 GB** |
| **Total** | **~30.7 GB** ❌ over 24 GB | **Total** | **~18.7 GB** ✅ |

## Multi-LoRA Migration Path

To migrate to the single-engine multi-LoRA topology, follow these steps:

1. Enable single engine mode in `mios.toml`:
   ```toml
   [converge.inference]
   heavy_engine_mode = "single"
   ```
2. Run `mios-sync-env` to apply the environment changes.
3. Restart `mios-llm-heavy` service to boot vLLM with multi-LoRA flags enabled:
   ```bash
   systemctl restart mios-llm-heavy.service
   ```
4. Verify the active adapters endpoint:
   ```bash
   curl http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1/inference/lora/list
   ```
5. Retire the secondary alternative service by updating your configuration:
   ```toml
   [converge.inference]
   retire_heavy_alt = true
   ```
6. Run `mios-sync-env` and disable the service:
   ```bash
   systemctl disable --now mios-llm-heavy-alt.service
   ```

## Rollback

To rollback to the dual-heavy configuration:

1. Update `mios.toml`:
   ```toml
   [converge.inference]
   heavy_engine_mode = "dual"
   retire_heavy_alt = false
   ```
2. Run `mios-sync-env`.
3. Restart `mios-llm-heavy.service` and re-enable/start the alternative service:
   ```bash
   systemctl enable --now mios-llm-heavy-alt.service
   ```

## Operator Note on Adapter Placement

LoRA adapter directories inside the container are mapped to the host directory defined in `[converge.inference].vllm_lora_adapters_dir` (defaulting to `/var/lib/mios/lora-adapters/`).
Operators must manually place or symlink GGUF/safetensors adapter weights inside `/var/lib/mios/lora-adapters/{coding,reasoning,vision}/` subdirectories on the host to make them available for dynamic loading.
