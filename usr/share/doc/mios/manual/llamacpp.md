<!-- AI-hint: Manual pages distilled from the source comments of llamacpp, sanitized, each passage anchored to the comment it came from. -->

# llamacpp

### ── chat / reasoning models (KV-pageable; --parallel 1 = one...

── chat / reasoning models (KV-pageable; --parallel 1 = one resident slot) ──
NOTE (fleet modernization): the light brain is now IBM Granite 4.1
8B (dense 'granite' arch) -- it loads on MAINLINE llama.cpp, which is the REAL
fix for the old qwen3.5:4b qwen35-arch block (custom "qwen35" arch failed:
"qwen35.rope.dimension_sections wrong array length; expected 4, got 3"). The
micro lane is Liquid AI LFM2-700M ('lfm2' arch, also mainline). No patched fork
is needed any more; every served arch here loads on stock llama-server.
The fleet is now FAMILY-DIVERSE (IBM Granite + Liquid AI + Google + H Company)
and 128k-on-every-chat-lane via symmetric q8_0 quantized KV cache + flash-attn.
micro_cpu (always-warm classify/expand/gate). fleet modernization:
qwen3:1.7b -> Liquid AI LFM2-700M (LiquidAI/LFM2-700M-GGUF). Dense 'lfm2' arch is
MERGED in mainline llama.cpp (PR #14620, ~b6709) -- NOT the qwen35 trap; verified
`llama-cli -hf LiquidAI/LFM2-700M-GGUF`. Beats Qwen3-0.6B on MMLU/GSM8K/IFEval at
~0.7-1.0GB resident, ~2x faster CPU decode. Family-diversity win (off Qwen).
CAVEAT: LFM2 tool-calls use a Pythonic special-token format, not OpenAI JSON --
fine for the short micro job, not a JSON-tool agent. Native ctx 32K (micro lane is
EXEMPT from the global 128k chat mandate). Symmetric q8_0 KV + flash-attn on (GPU-
offloadable; NEVER asymmetric k!=v -> issue #20866 CPU-fallback). The qwen3:1.7b
alias keeps every pipeline-emitted micro name resolving onto the new model.
Part 10 CONV-04: --cache-reuse 256 (gate: MIOS_CONVERGE_INFERENCE_LLAMA_CACHE_REUSE_TOKENS > 0); --np 4 for shared-prefix concurrency.

<!-- mios-src:47bbd3a1697e from usr/share/mios/llamacpp/mios-llm-light.yaml:39-58 -->

### light_brain + coder -- IBM Granite 4.1 8B Instruct (dense)....

light_brain + coder -- IBM Granite 4.1 8B Instruct (dense). fleet
modernization: the served chat/reasoning GGUF moves from gemma4:12b to Granite 4.1
8B (ibm-granite/granite-4.1-8b; GGUF unsloth/granite-4.1-8b-GGUF Q4_K_M ~5.5GB).
Dense GraniteForCausalLM ('granite') arch loads on MAINLINE llama.cpp (bartowski
quant @ b8970; Unsloth uses stock llama-server) -> sidesteps the qwen35 trap that
BLOCKS qwen3.5:4b and forced the gemma4:12b fallback. Granite is natively 131K-capable,
but the SERVED --ctx-size is 32768 (model-placement decision): the
always-on SGLang heavy lane ([ai.sglang], mem_fraction 0.45 ~= 11GB RESERVED up front) +
granite weights (~5GB) + a full 128k q8_0 KV (~8GB) CANNOT co-fit the shared 24GB 4090 ->
granite's load spilled to CPU = 67-100s refine (the chat-slowness root cause). A 32k q8_0
KV (~2-3GB) co-fits SGLang on the GPU (refine drops to ~2-5s) and is ample for chat/refine.
Raise back toward 131072 ONLY if the SGLang heavy lane is disabled or moved off this card
(then granite has the whole 4090). Apache-2.0; first non-Qwen brain (family mix).
DO NOT pull the '-h'/granitehybrid GGUF (newer arch = qwen35-style trap). Build
note: avoid CUDA 13.2 (gibberish-output bug). --jinja MANDATORY for tool_calls.
128k FIT: symmetric q8_0 K+V quantized KV cache + flash-attn on (GPU-offloadable;
NEVER asymmetric k!=v -> issue #20866 ~40x CPU-fallback). Low-end 8GB profile:
drop --cache-type-k/-v to q4_0 in the /etc overlay.
Part 10 CONV-04: --cache-reuse 256 (gate: MIOS_CONVERGE_INFERENCE_LLAMA_CACHE_REUSE_TOKENS > 0); --np 4 for shared-prefix concurrency.

<!-- mios-src:b2aa5f67cf2f from usr/share/mios/llamacpp/mios-llm-light.yaml:72-90 -->

### Alias every legacy/role model name the pipeline still emits...

Alias every legacy/role model name the pipeline still emits (incl. the old
gemma4:12b reasoning tag and the BLOCKED qwen3.5 tags) onto Granite 4.1 8B so
the upstream llama-swap proxy routes them instead of 400 "no router for requested model". Until G7
reconciles names in SSOT this resolves the whole pipeline to the one served
mainline-loadable brain GGUF on the dGPU.

<!-- mios-src:51b232ac18a0 from usr/share/mios/llamacpp/mios-llm-light.yaml:92-96 -->

### ── embeddings (replaces the legacy embed lane; OpenAI...

── embeddings (replaces the legacy embed lane; OpenAI /v1/embeddings) ──
fleet modernization: nomic-embed-text -> Google EmbeddingGemma-300m QAT
(ggml-org/embeddinggemma-300m-qat-q8_0-GGUF). 768d NATIVE = exact pgvector
vector(768) match -> ZERO schema migration. QAT (operator efficiency win);
'gemma-embedding' arch merged mainline (b6384); <0.5GB, CPU-servable. The served
NAME stays "nomic-embed-text" so every caller (OWUI, knowledge/RAG, the 768d
column) is unchanged -- only the backing GGUF swaps. CAVEAT: set --pooling mean
explicitly (issue #19040 cosine-drift was a mean-vs-CLS config error, not arch).
MIGRATION GATE: run the knowledge-recall sanity test (0.933-hit) before retiring
the real nomic GGUF; to roll back, point --model at /models/nomic-embed-text.gguf.
Embeddings are EXEMPT from the 128k mandate -- ctx stays short (2048).

<!-- mios-src:8cdbdd97b150 from usr/share/mios/llamacpp/mios-llm-light.yaml:128-138 -->

### parallel 4 (F1): the embedding server had ONE slot, so a...

-parallel 4 (F1): the embedding server had ONE slot, so a
BURST of concurrent /v1/embeddings (OWUI re-vectorizing a knowledge collection;
any multi-chunk RAG batch) overran it -> llama-swap returned 429 Too Many Requests
and the MiOS Documentation collection never vectorized (knowledge_search 0 hits).
4 embedding slots absorb the concurrency; the 300m model is tiny so the extra KV
is negligible. Pairs with the co-resident `swap:false` group (which fixed the
SWAP-induced 429); this fixes the CONCURRENCY-induced one.

<!-- mios-src:f0e2e4fcc39a from usr/share/mios/llamacpp/mios-llm-light.yaml:140-146 -->

### ── vision grounding (cu_ground / mios-pc-vision fallback)...

── vision grounding (cu_ground / mios-pc-vision fallback) ──────────────────
INERT until BOTH GGUFs exist under /models -- the operator downloads them (the
security classifier blocks the fetch for the assistant). Once present, mios-llm-light
serves qwen3-vl:4b with vision on demand and cu_ground's vision fallback
activates (endpoint already points here, the `llm_light` port;).
fleet modernization: qwen3-vl:4b -> H Company Holo1.5-7B (UI-grounding
SFT, ScreenSpot-Pro ~57.9). GGUF mradermacher/Holo1.5-7B-GGUF Q4_K_M 4.68GB +
mmproj-Q8_0 1.0GB. Base arch 'qwen2vl' (fine-tune of Qwen2.5-VL) is MATURE on
mainline llama.cpp -> opposite of the qwen35 fork-only trap. ~5.7GB fits the 4090.
Keep mmproj >= Q8_0/f16 (vision is quant-sensitive). Low-end 8GB profile: use the
3B (mradermacher/Holo1.5-3B-GGUF Q4_K_M 2.1GB + mmproj-Q8_0 0.9GB, ~3GB total).
Vision/grounding lane keeps short ctx (8192) -- EXEMPT from the 128k chat mandate.
STILL INERT until BOTH GGUFs exist under /models (operator places them; the
security classifier blocks the assistant fetch). The served NAME stays
"qwen3-vl:4b" so cu_ground / mios-pc-vision endpoints are unchanged.

<!-- mios-src:43f40c467ca1 from usr/share/mios/llamacpp/mios-llm-light.yaml:154-168 -->

### ── CO-RESIDENT GROUP ("explain the logs" exposed it) ──────...

── CO-RESIDENT GROUP ("explain the logs" exposed it) ──────
Default llama-swap is single-active: only ONE model resident, the rest swapped
out. So an embeddings request (RAG / memory) that arrives DURING a chat turn had
to swap the BUSY chat model out -- which llama-swap refuses mid-request and
returns 429 Too Many Requests. Result: ~40% of /v1/embeddings 429'd on a normal
turn (the agent-pipe stores knowledge + RAG-enriches every turn), silently
degrading memory/recall. FIX: keep the everyday trio CO-RESIDENT. `swap: false`
= members never evict EACH OTHER (all can be loaded together); they still load
on demand. VRAM: granite4.1:8b ~5.5G + lfm2:700m ~0.7G + embeddinggemma ~0.5G
= ~6.7G, far inside the 4090's 24G. Vision (qwen3-vl, inert/gated) stays in the
implicit default group and swaps in on demand (exclusive), evicting this group
only when actually used. No more swap-contention 429 on the hot chat+embed path.

<!-- mios-src:1be2f70bf455 from usr/share/mios/llamacpp/mios-llm-light.yaml:179-190 -->
### ── chat / reasoning models (KV-pageable; --parallel 1 = one...

── chat / reasoning models (KV-pageable; --parallel 1 = one resident slot) ──
NOTE (fleet modernization): the light brain is now IBM Granite 4.1
8B (dense 'granite' arch) -- it loads on MAINLINE llama.cpp, which is the REAL
fix for the old qwen3.5:4b qwen35-arch block (custom "qwen35" arch failed:
"qwen35.rope.dimension_sections wrong array length; expected 4, got 3"). The
micro lane is Liquid AI LFM2-700M ('lfm2' arch, also mainline). No patched fork
is needed any more; every served arch here loads on stock llama-server.
The fleet is now FAMILY-DIVERSE (IBM Granite + Liquid AI + Google + H Company)
and 128k-on-every-chat-lane via symmetric q8_0 quantized KV cache + flash-attn.
micro_cpu (always-warm classify/expand/gate). fleet modernization:
qwen3:1.7b -> Liquid AI LFM2-700M (LiquidAI/LFM2-700M-GGUF). Dense 'lfm2' arch is
MERGED in mainline llama.cpp (PR #14620, ~b6709) -- NOT the qwen35 trap; verified
`llama-cli -hf LiquidAI/LFM2-700M-GGUF`. Beats Qwen3-0.6B on MMLU/GSM8K/IFEval at
~0.7-1.0GB resident, ~2x faster CPU decode. Family-diversity win (off Qwen).
CAVEAT: LFM2 tool-calls use a Pythonic special-token format, not OpenAI JSON --
fine for the short micro job, not a JSON-tool agent. Native ctx 32K (micro lane is
EXEMPT from the global 128k chat mandate). Symmetric q8_0 KV + flash-attn on (GPU-
offloadable; NEVER asymmetric k!=v -> issue #20866 CPU-fallback). The qwen3:1.7b
alias keeps every pipeline-emitted micro name resolving onto the new model.
Part 10 CONV-04: --cache-reuse 256 (gate: MIOS_CONVERGE_INFERENCE_LLAMA_CACHE_REUSE_TOKENS > 0); --np 4 for shared-prefix concurrency.

<!-- mios-src:4d938bc91299 from usr/share/mios/llamacpp/mios-llm-light.yaml:4-23 -->

### light_brain + coder -- IBM Granite 4.1 8B Instruct (dense)....

light_brain + coder -- IBM Granite 4.1 8B Instruct (dense). fleet
modernization: the served chat/reasoning GGUF moves from gemma4:12b to Granite 4.1
8B (ibm-granite/granite-4.1-8b; GGUF unsloth/granite-4.1-8b-GGUF Q4_K_M ~5.5GB).
Dense GraniteForCausalLM ('granite') arch loads on MAINLINE llama.cpp (bartowski
quant @ b8970; Unsloth uses stock llama-server) -> sidesteps the qwen35 trap that
BLOCKS qwen3.5:4b and forced the gemma4:12b fallback. Granite is natively 131K-capable,
but the SERVED --ctx-size is 32768 (model-placement decision): the
heavy lane on its SGLang engine ([ai.sglang], mem_fraction 0.45 ~= 11GB RESERVED up front) +
granite weights (~5GB) + a full 128k q8_0 KV (~8GB) CANNOT co-fit the shared 24GB 4090 ->
granite's load spilled to CPU = 67-100s refine (the chat-slowness root cause). A 32k q8_0
KV (~2-3GB) co-fits the heavy lane on the GPU (refine drops to ~2-5s) and is ample for chat/refine.
Raise back toward 131072 ONLY if the heavy lane is disabled or moved off this card
(then granite has the whole 4090). Apache-2.0; first non-Qwen brain (family mix).
DO NOT pull the '-h'/granitehybrid GGUF (newer arch = qwen35-style trap). Build
note: avoid CUDA 13.2 (gibberish-output bug). --jinja MANDATORY for tool_calls.
128k FIT: symmetric q8_0 K+V quantized KV cache + flash-attn on (GPU-offloadable;
NEVER asymmetric k!=v -> issue #20866 ~40x CPU-fallback). Low-end 8GB profile:
drop --cache-type-k/-v to q4_0 in the /etc overlay.
Part 10 CONV-04: --cache-reuse 256 (gate: MIOS_CONVERGE_INFERENCE_LLAMA_CACHE_REUSE_TOKENS > 0); --np 4 for shared-prefix concurrency.

<!-- mios-src:b386057e8363 from usr/share/mios/llamacpp/mios-llm-light.yaml:54-72 -->
