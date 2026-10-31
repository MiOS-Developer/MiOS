<!-- AI-hint: Manual pages distilled from the source comments of ai, sanitized, each passage anchored to the comment it came from. -->

# ai

### Autonomous Self-Healing Code Remediation Agent (task_326 /...

Autonomous Self-Healing Code Remediation Agent (task_326 / task_2559)

Listens for and detects systemd unit failure events, harvests recent journald error logs,
formulates structured root cause diagnoses, enforces circuit breaker rate limiting
(max 3 restarts / 15m), strictly protects immutable `/usr` partitions (Architectural Law 1),
applies safe `/etc` configuration patches and `/var` repairs, and logs RCA records
to `/var/log/mios/self-heal.log`.

<!-- mios-src:729c98cd7e63 from usr/libexec/mios/ai/self_heal.py:4-12 -->

### Enforces Architectural Law 1 (USR-OVER-ETC) & bootc...

Enforces Architectural Law 1 (USR-OVER-ETC) & bootc immutability.
    Strictly forbids modifications to /usr and ensures all mutations are
    scoped to /etc overrides, /var runtime storage, or transient /tmp paths.

<!-- mios-src:4a3daad55758 from usr/libexec/mios/ai/self_heal.py:191-195 -->

### Formulates structured root cause diagnosis from failure...

Formulates structured root cause diagnosis from failure event and journal logs.

<!-- mios-src:5a5c0476a80a from usr/libexec/mios/ai/self_heal.py:351-353 -->

### Synthetic Training Q&A Data Pipeline (task_327 / task_2560)...

Synthetic Training Q&A Data Pipeline (task_327 / task_2560)

Harvests architectural chapters, user guides, manual pages, and ADRs from `/usr/share/doc/mios/`
and `field/`, performs hierarchical markdown parsing with context preservation, synthesizes
multi-turn reasoning and domain-specific Q&A pairs for `mios-opencode` fine-tuning,
enforces secret/token redaction (Rule 14), and emits JSONL datasets to `/var/lib/mios/ai/dataset/`.

<!-- mios-src:33fe7e6e8b1a from usr/libexec/mios/ai/synthetic_qa.py:4-11 -->
### fp8_kv_quant.py — task_707 WS-AI Dynamic FP8 (E4M3) KV-cache...

fp8_kv_quant.py — task_707 WS-AI
Dynamic FP8 (E4M3) KV-cache quantizer and per-head scale manager in llama-swap.

Quantizes KV tensors to 8-bit FP8 (E4M3) format with dynamic per-head scaling,
halving VRAM consumption while preserving needle retrieval accuracy.

<!-- mios-src:c26b4eb993e5 from usr/lib/mios/ai/fp8_kv_quant.py:4-10 -->

### intel_paged_attn.py — task_713 WS-VFIO Intel oneAPI Level Zero...

intel_paged_attn.py — task_713 WS-VFIO
Intel oneAPI Level Zero PagedAttention engine and XMX SYCL matrix kernels in IPEX.

Configures Level Zero runtime (ZE_ENABLE_PCI_ID_DEVICE_ORDER=1) and 16-token
PagedAttention virtual blocks on Intel Arc/Battlemage GPUs (>90% VRAM efficiency).

<!-- mios-src:dcfc8ac4dd4e from usr/lib/mios/ai/intel_paged_attn.py:4-10 -->

### kquants_slicer.py — task_717 WS-AI Dynamic K-Quants...

kquants_slicer.py — task_717 WS-AI
Dynamic K-Quants mixed-precision layer slicer (Q4_K_M / Q5_K_M / Q6_K) in llama-swap.

Retains Q5_K_M/Q6_K precision for attention heads and slices FFN matrices to Q4_K_M,
fitting 32B models into <16GB VRAM budgets with <0.03 perplexity delta.

<!-- mios-src:959026e1ce98 from usr/lib/mios/ai/kquants_slicer.py:4-10 -->

### Slice plan

Slice plan: tensor group -> (target K-Quant precision, MiB of VRAM per billion
model parameters). The MiB/B coefficients are the measured footprint of the 32B
reference build (3500/3200/2800/2900/2800 MiB) divided by its 32B parameter
count, so they already carry each K-Quant type's per-block scale/min metadata.
At a fixed quantization the footprint is linear in parameter count, so scaling
these by the configured model size is exact across the family -- which is the
point: the five per-tensor values used to be 32B-only literals, and a slicer
built for a 7B or a 70B still reported the 32B budget.

<!-- mios-src:ccda7a246451 from usr/lib/mios/ai/kquants_slicer.py:19-26 -->

### mxfp4_kv_quant.py — task_715 WS-AI Microscaling MXFP4 (E2M1)...

mxfp4_kv_quant.py — task_715 WS-AI
Microscaling MXFP4 (E2M1) KV-cache quantizer and block-32 scale vector manager.

Groups vector elements into 32-value blocks with shared 8-bit scale factor (E8M0),
reducing KV-cache VRAM allocation to <=27% of uncompressed FP16 (4x density).

<!-- mios-src:c29210af3cd3 from usr/lib/mios/ai/mxfp4_kv_quant.py:4-10 -->

### speculative_prune.py — task_679 WS-AI In-place tree branch...

speculative_prune.py — task_679 WS-AI
In-place tree branch bitmask pruner and speculative KV compaction kernel.

Applies a 16-bit branch mask to reset unaccepted KV block pointers in the
virtual page table and advance active sequence length counter in-place with
zero host-GPU synchronization stalls.

<!-- mios-src:9167244a85ea from usr/lib/mios/ai/speculative_prune.py:4-11 -->

### streaming_llm.py — task_699 WS-AI StreamingLLM attention sink...

streaming_llm.py — task_699 WS-AI
StreamingLLM attention sink pinner and rolling KV eviction manager in llama-swap.

Pins initial attention sink tokens (positions 0..3) permanently while maintaining
a rolling FIFO circular buffer for positions >= 4, bounding memory consumption
and enabling perpetual infinite generation with zero OOM crashes.

<!-- mios-src:dcec017579a0 from usr/lib/mios/ai/streaming_llm.py:4-11 -->

### Eviction is per-token on an infinite generation, so it has...

Eviction is per-token on an infinite generation, so it has to be O(1).
A list evicts the head in O(n) -- at a 32k window that is a 32k-element
memmove for every single token. A bounded deque pops the head in O(1),
and its maxlen makes the window a property of the structure rather than
of the one call site that remembers to trim.

<!-- mios-src:1357f284086c from usr/lib/mios/ai/streaming_llm.py:35-39 -->

### tensor_pipeline.py — task_918 WS-AI Distributed pipeline...

tensor_pipeline.py — task_918 WS-AI
Distributed pipeline tensor dispatcher with dynamic RPC worker layer partitioning.

Calculates activation tensor network payloads, splits 80-layer models (Llama-3.1-70B)
across local GPUs and remote RPC worker blades, and manages mid-inference failover.

<!-- mios-src:d776fcb9447d from usr/lib/mios/ai/tensor_pipeline.py:4-10 -->

### WS-AI (task_497): Cryptographic Merkle-Tree Agent Audit Chain...

WS-AI (task_497): Cryptographic Merkle-Tree Agent Audit Chain Recorder & Ed25519 Block Signer.
Maintains an immutable, append-only cryptographic audit chain for all agent decisions,
tool invocations, and filesystem modifications. Each block cryptographically binds to its predecessor
via SHA-256 hash chains, signs the block hash using the host node's Ed25519 key, and constructs
periodic Merkle tree roots for lightweight inclusion proof verification and tamper detection.

<!-- mios-src:e2460fc9be3e from usr/libexec/mios/ai/audit_chain.py:5-11 -->

### Verify complete cryptographic chain continuity: 1. Genesis...

Verify complete cryptographic chain continuity:
        1. Genesis block correctness.
        2. Hash chain continuity (block[i].prev_hash == block[i-1].block_hash).
        3. Payload hash integrity.
        4. Block hash integrity.
        5. Ed25519 signature validity.

<!-- mios-src:6abb440c042d from usr/libexec/mios/ai/audit_chain.py:326-333 -->

### mios_asr.py — task_681 WS-AI Streaming CTC / Conformer ONNX...

mios_asr.py — task_681 WS-AI
Streaming CTC / Conformer ONNX speech recognition daemon and VAD chunker.

Processes 30ms audio windows through quantized Silero VAD, streams voiced PCM
chunks into quantized Conformer ONNX encoder, and emits streaming partial text
tokens over socket with sub-100ms latency.

<!-- mios-src:4e382e754534 from usr/libexec/mios/ai/mios_asr.py:5-12 -->

### WS-AI (task_515): Hardware-Tiered Modern Model Matrix...

WS-AI (task_515): Hardware-Tiered Modern Model Matrix Allocator for Consumer, Prosumer, and Poweruser.
Dynamically detects host GPU VRAM and System RAM to assign modern open-weight models
(Qwen2.5-Coder, DeepSeek-R1-Distill, nomic-embed-text) across function-named inference lanes
(mios-llm-light, mios-llm-heavy). Enforces strict VRAM headroom reservation (<=90%) and projects
configurations into llama-swap.yaml for zero-downtime multi-model auto-swapping.

<!-- mios-src:ca7edf7210b3 from usr/libexec/mios/ai/model_matrix_alloc.py:5-11 -->

### quant_dispatch.py — task_701 WS-AI Dynamic quantization kernel...

quant_dispatch.py — task_701 WS-AI
Dynamic quantization kernel auto-dispatcher (Marlin / ExLlamaV2 / GGUF).

Inspects incoming model weight format (Marlin, AWQ, GPTQ, GGUF) and GPU hardware
architecture, dynamically binding the fastest engine for >3.5x token decoding speedup.

<!-- mios-src:62e9cd34be78 from usr/libexec/mios/ai/quant_dispatch.py:4-10 -->
### Synthetic Training Q&A Data Pipeline (task_327 / task_2560)...

Synthetic Training Q&A Data Pipeline (task_327 / task_2560)

Harvests architectural chapters, user guides, manual pages, and ADRs from `/usr/share/doc/mios/`
including its `adr/` records, performs hierarchical markdown parsing with context preservation, synthesizes
multi-turn reasoning and domain-specific Q&A pairs for `mios-opencode` fine-tuning,
enforces secret/token redaction (Rule 14), and emits JSONL datasets to `/var/lib/mios/ai/dataset/`.

<!-- mios-src:27a8e4e1bc88 from usr/libexec/mios/ai/synthetic_qa.py:4-11 -->
### Ed25519 signer bound to ONE persisted node key...

Ed25519 signer bound to ONE persisted node key (``key_path``, raw 32-byte seed, 0600).

    The key is generated on first sign and must be persisted before it is used:
    a key that is not on disk can never be re-verified by a later run, so a
    block signed with it would silently break the chain. ``mock`` is a test seam
    only; it is honoured solely when the caller passes ``mock=True``.

<!-- mios-src:1b6b1ee6096e from usr/libexec/mios/ai/audit_chain.py:48-54 -->
