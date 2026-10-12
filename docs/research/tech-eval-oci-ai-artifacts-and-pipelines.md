<!-- AI-hint: Research evaluation of upstream FOSS patterns for OCI-packaged AI artifacts and automated CI/CD pipelines. -->
# Technology & Architecture Evaluation: Upstream FOSS Patterns for OCI/Container-Based AI Artifacts & Automated CI/CD Pipelines

**Evaluation Topic:** Upstream FOSS Patterns for OCI/Container-Based AI Artifacts (ModelWeights, Datasets, Prompts, Adapters) and Automated CI/CD Packaging Pipelines  
**Author:** MiOS Core Architecture & Antigravity Agentic Systems  
**Date:** 2026-09-26  
**Status:** PROPOSED / APPROVED FOR ARCHITECTURAL BASELINE  
**Related Epics / Tasks:** task_1022 (ARTIFACT-03: Daily out-of-loop artifact follow-ups), SPIKE-20260924 (Artifact Publisher Contracts), Law 1 (FHS), Law 3 (.git IS /), Law 5 (UNIFIED-AI-REDIRECTS), Law 14 (TARGET-LANGUAGES), Law 16 (ONE-TEMPLATE-PER-TYPE)

---

## 1. Problem Statement & Operational Constraints

### 1.1 The Operational Challenge
In cloud-native and immutable agentic operating systems, AI models, datasets, and fine-tuning artifacts have historically been managed out-of-band using ad-hoc object stores (AWS S3, Google Cloud Storage), unversioned network shares, or proprietary model hubs (Hugging Face Git-LFS). This fragmented approach creates critical operational failure modes:
1. **Cold-Start Latency & Network Bottlenecks:** Naive container initialization scripts dynamically downloading 10GB–70GB model weights at pod startup stall autoscaling, consume excessive node egress, and fail unpredictably when network blips occur.
2. **Incomplete OCI Image Layouts & Dangling References:** Emitting OCI image archives without strict Merkle closure (such as index-only archives referencing blobs absent from the tarball, documented in `SPIKE-20260924-artifact-publisher-contracts`) leads to runtime failure during image extraction or `bootc switch`.
3. **Broken Provenance & Lack of Traceability:** Fine-tuned LoRA adapters and quantization cuts frequently lose their lineage back to the precise dataset git commit, tokenizer configuration, training hyperparameters, and base foundation model digest.
4. **Supply Chain Vulnerability & Arbitrary Code Execution:** Traditional Python-centric serialization formats (`.pkl`, `.bin`, PyTorch `torch.save()`) allow arbitrary code execution during deserialization. Furthermore, AI training datasets frequently risk ingesting unscrubbed credentials, PII, and poisoned synthetic examples without automated CI/CD gating.

### 1.2 Non-Negotiable Operational Constraints for MiOS
- **OCI Standard Primacy:** Strict compliance with the Open Container Initiative (OCI) Image Specification v1.1.1 and Distribution Specification v1.1.1. No proprietary client protocols or non-standard registries.
- **Universal OpenAI-API Alignment (Law 5):** All metadata, prompt representations, tool definitions, and dataset schemas must speak OpenAI-compatible interfaces (`/v1/chat/completions`, OpenAI Chat SFT format, OpenAI Preference DPO format).
- **Substrate & Driver Invariants:** Seamless execution on immutable bootc/ostree host environments; zero-copy memory mapping (`mmap`) support; compatibility with container runtimes (`podman` 5.8+, `buildah` 1.43+, `skopeo` 1.22+).
- **Standing Gate Enforcement:** Any artifact emitted by automated pipelines must clear automated Merkle descriptor closure, safe-deserialization validation, PII/secret scrubbing, and cryptographic signing before publication.

---

## 2. Primary Sources & Upstream Specifications

This research synthesizes the canonical upstream specifications, open standards, and production implementations:

| Standard / Project | Governance / Vendor | Canonical Reference | Installed / Upstream Version | Key Specification Focus |
| :--- | :--- | :--- | :--- | :--- |
| **OCI Image Specification** | Open Container Initiative (Linux Foundation) | [opencontainers/image-spec](https://github.com/opencontainers/image-spec) | v1.1.1 (Supported natively by `buildah` 1.43.4) | `artifactType`, content descriptors, empty config descriptor, image layout |
| **OCI Distribution Spec** | Open Container Initiative (Linux Foundation) | [opencontainers/distribution-spec](https://github.com/opencontainers/distribution-spec) | v1.1.1 | Referrers API (`/v2/<name>/referrers/<digest>`), cross-repository blob mounting |
| **KitOps / ModelKit** | CNCF Sandbox | [KitOps Documentation](https://kitops.ml/) / [GitHub](https://github.com/jozu-ai/kitops) | v1.0.0 (Kitfile Specification v1.0.0) | Declarative `Kitfile` YAML packaging model, dataset/code/prompt multi-layering |
| **KServe ModelCar** | CNCF Incubating (Kubernetes AI) | [KServe ModelCar Guide](https://kserve.github.io/website/latest/modelserving/storage/modelcar/) | KServe v0.13+ / v0.14+ | OCI image filesystem packaging, `storageUri: oci://...`, shared pod volume mount |
| **Ollama Model Manifest** | Ollama (FOSS) | [Ollama Source](https://github.com/ollama/ollama) | v0.5.x+ | Custom layer media types (`application/vnd.ollama.image.*`), content-addressed blobs |
| **ORAS** | CNCF Incubating | [ORAS Project](https://oras.land/) / [GitHub](https://github.com/oras-project/oras) | v1.2.2+ | OCI Registry As Storage CLI/library for arbitrary file types and artifact graphs |
| **SafeTensors** | Hugging Face / Apache-2.0 | [huggingface/safetensors](https://github.com/huggingface/safetensors) | v0.4.5+ | Fast zero-copy tensor storage with JSON header and safe mmap; anti-pickle |
| **GGUF** | llama.cpp / MIT | [ggml/gguf](https://github.com/ggerganov/ggml/blob/master/docs/gguf.md) | GGUF v3 | Single-file multi-tensor binary container with baked KV metadata and tensor shapes |
| **Sigstore / Cosign** | OpenSSF / Linux Foundation | [sigstore/cosign](https://github.com/sigstore/cosign) | v2.4.1+ | Keyless signing, in-toto SLSA attestation, OCI 1.1 referrers attachment |
| **Croissant / AIBOM** | MLCommons & CycloneDX | [MLCommons Croissant](https://mlcommons.org/croissant/) / [CycloneDX AI](https://cyclonedx.org/) | CycloneDX v1.6 (AI/ML Extension) | Dataset documentation metadata, model card lineage, training hyperparameter SBOM |

---

## 3. Upstream Architectural Patterns Deconstruction

Four primary architectural patterns have emerged in the FOSS ecosystem for structuring, packaging, and distributing AI artifacts via OCI registries.

```
+----------------------------------------------------------------------------------------------------+
|                                  UPSTREAM OCI AI ARTIFACT TAXONOMY                                 |
+----------------------------------------------------------------------------------------------------+
| 1. CNCF KitOps ModelKit        | 2. KServe ModelCar          | 3. Ollama OCI Model  | 4. ORAS OCI 1.1 Graph |
| (Declarative Multi-Layer)      | (Filesystem Container)      | (Native LLM Runtime) | (Pure Referrers DAG)  |
|--------------------------------+-----------------------------+----------------------+-----------------------|
| - Top: Kitfile (YAML)          | - Top: Containerfile (Root) | - Top: Modelfile     | - Top: Root Manifest  |
| - Layer 1: Model weights (tar) | - Single/Multi rootfs layer | - Layer 1: GGUF Base | - Layer 1: Model Blob |
| - Layer 2: Dataset (tar)       |   (/models/... files)       | - Layer 2: Template  | - Layer 2: JSONL Data |
| - Layer 3: Code/Notebooks(tar) | - Standard OCI Image layers | - Layer 3: Params    | - Subject 1: Attest   |
| - Layer 4: Prompts/Docs (tar)  | - Extracted by runtime      | - Layer 4: License   | - Subject 2: Cosign   |
+----------------------------------------------------------------------------------------------------+
```

### 3.1 Pattern A: CNCF KitOps / ModelKit (`Kitfile`)
KitOps treats AI projects analogously to how container engines treat applications. It introduces a declarative manifest called the **`Kitfile`**, specifying components that the `kit` CLI compiles into a standards-compliant OCI v1.1 artifact called a **ModelKit**.

#### Structure of a `Kitfile`:
```yaml
manifestVersion: v1.0.0

package:
  name: mios-coder-7b
  version: 0.3.0
  description: "MiOS localized coder model and fine-tuning SFT dataset"
  license: Apache-2.0
  authors:
    - MiOS Core Team <mios.helpdesk@proton.me>

model:
  name: qwen2.5-coder-7b-instruct
  path: ./models/qwen2.5-coder-7b.gguf
  framework: llama.cpp
  version: 3.0.0
  description: "Quantized GGUF Q4_K_M for local inference lane mios-llm-light"
  license: Apache-2.0

datasets:
  - name: mios-sft-corpus
    path: ./datasets/sft.jsonl
    description: "System grounding SFT records generated from manual-corpus.tsv"
  - name: mios-dpo-preferences
    path: ./datasets/dpo.jsonl
    description: "Direct Preference Optimization pairs enforcing architectural laws"

code:
  - name: finetune-recipes
    path: ./tools/finetune/
    description: "LoRA training driver scripts and hyperparameter templates"
```

#### OCI Packaging Mechanics:
- When running `kit pack . -t registry.internal:5000/models/mios-coder:0.3.0`, the CLI packages each section (`model`, `datasets`, `code`) into independent content-addressed uncompressed or gzip tarball layers.
- Manifest uses standard OCI v1.1 properties:
  - `artifactType: application/vnd.kitops.modelkit.v1+json`
  - Layer media types:
    - `application/vnd.kitops.modelkit.model.v1+tar`
    - `application/vnd.kitops.modelkit.dataset.v1+tar`
    - `application/vnd.kitops.modelkit.code.v1+tar`
- **Selective Retrieval:** Downstream consumers can pull *only* what they need using `kit unpack --model` or `kit unpack --datasets`, avoiding multi-gigabyte transfers when only prompt or dataset updates are required.

---

### 3.2 Pattern B: CNCF KServe "ModelCar" (Container Image Filesystem)
The "ModelCar" pattern, widely adopted across Red Hat OpenShift AI, Google Cloud Kubernetes Engine, and KServe, packages raw model weights directly inside the standard POSIX filesystem of an OCI container image (e.g., under `/models/` or `/model/`).

#### Containerfile Specification:
```dockerfile
FROM scratch
LABEL org.opencontainers.image.title="mios-llm-heavy-qwen-72b"
LABEL org.opencontainers.image.version="0.3.0"
LABEL org.opencontainers.image.description="vLLM SafeTensors weights for mios-llm-heavy"
LABEL ai.mios.format="safetensors"
LABEL ai.mios.context_length="32768"

# Model weights and configuration placed in standard FHS directory
COPY --chown=0:0 ./models/qwen-72b-instruct/ /models/
```

#### Deployment & Runtime Mechanics:
- Deployed in Kubernetes or Podman via volume sharing:
  ```yaml
  apiVersion: serving.kserve.io/v1beta1
  kind: InferenceService
  metadata:
    name: mios-heavy
  spec:
    predictor:
      model:
        storageUri: oci://registry.internal:5000/models/qwen-72b:latest
        runtime: vllm
  ```
- **Execution Workflow:** The container engine pulls the ModelCar image onto the host node. An init container or runtime sidecar mounts the `/models` directory from the ModelCar container into a shared memory/disk volume accessible to the inference engine (vLLM, TGI, or llama-swap).
- **Benefits:**
  - Zero bespoke tooling required: works natively with standard `podman pull`, `buildah bud`, `skopeo copy`, and Quay/Harbor registries.
  - Leverages node-level container image layer caching: subsequent pod or Quadlet restarts do not re-download weights.

---

### 3.3 Pattern C: Ollama OCI Layered Model Distribution
Ollama pioneered storing large language models inside standard OCI/Docker container registries by deconstructing a `Modelfile` into specialized functional layers.

#### OCI Manifest Representation:
```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
  "config": {
    "mediaType": "application/vnd.docker.container.image.v1+json",
    "digest": "sha256:d55f9a6...",
    "size": 408
  },
  "layers": [
    {
      "mediaType": "application/vnd.ollama.image.model",
      "digest": "sha256:4b9e28...",
      "size": 4683055104
    },
    {
      "mediaType": "application/vnd.ollama.image.template",
      "digest": "sha256:c18b7a...",
      "size": 1542
    },
    {
      "mediaType": "application/vnd.ollama.image.params",
      "digest": "sha256:8b34f2...",
      "size": 128
    },
    {
      "mediaType": "application/vnd.ollama.image.system",
      "digest": "sha256:e6819c...",
      "size": 845
    },
    {
      "mediaType": "application/vnd.ollama.image.license",
      "digest": "sha256:0192df...",
      "size": 11357
    }
  ]
}
```

#### Layer Semantics:
- `application/vnd.ollama.image.model`: The raw binary GGUF tensor weights.
- `application/vnd.ollama.image.template`: The chat completion prompt template formatted in Go templating syntax.
- `application/vnd.ollama.image.params`: JSON object carrying runtime hyperparameter defaults (`temperature`, `top_k`, `stop`, `num_ctx`).
- `application/vnd.ollama.image.system`: The default system prompt string.
- **Local Storage:** Blobs are stored unextracted in content-addressable storage (`~/.ollama/models/blobs/sha256-...`). When running a model, Ollama reads the GGUF header and passes the file descriptor to `llama.cpp` for instant `mmap` loading.

---

### 3.4 Pattern D: Pure OCI v1.1 Artifact Manifest with Referrers (ORAS / Sigstore)
Under the official OCI Image Specification v1.1.0+, non-container artifacts are represented directly using the top-level `artifactType` field, while auxiliary metadata (SBOMs, in-toto attestations, fine-tuned adapters, signatures) are linked via the `subject` field, forming an immutable Directed Acyclic Graph (DAG).

```
+----------------------------------------------------------------------------------+
|                     OCI v1.1 REFERRERS GRAPH ARCHITECTURE                        |
+----------------------------------------------------------------------------------+
|                                                                                  |
|                         [ Foundation Model Manifest ]                            |
|                         artifactType: ai.model.safetensors                       |
|                         Digest: sha256:BASE_MODEL_SHA                            |
|                                       ^                                          |
|                                       | (subject)                                |
|        +------------------------------+------------------------------+           |
|        |                                                             |           |
|  [ LoRA Delta Adapter ]                                       [ AIBOM / SBOM ]   |
|  artifactType: ai.model.adapter.peft                          artifactType:      |
|  Digest: sha256:ADAPTER_SHA                                   spdx+json          |
|        ^                                                             ^           |
|        | (subject)                                                   | (subject) |
|  [ Cosign Signature ]                                         [ Cosign Sig ]     |
|  artifactType: sigstore.cosign                                artifactType:      |
|                                                               sigstore.cosign    |
+----------------------------------------------------------------------------------+
```

#### Manifest JSON Example (Base Model):
```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.manifest.v1+json",
  "artifactType": "application/vnd.cncf.ai.model.v1.safetensors",
  "config": {
    "mediaType": "application/vnd.oci.empty.v1+json",
    "digest": "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    "size": 2
  },
  "layers": [
    {
      "mediaType": "application/vnd.cncf.ai.model.weights.v1.safetensors",
      "digest": "sha256:9f837...",
      "size": 15420384200,
      "annotations": {
        "org.opencontainers.image.title": "model.safetensors"
      }
    },
    {
      "mediaType": "application/json",
      "digest": "sha256:1a2b3...",
      "size": 1024,
      "annotations": {
        "org.opencontainers.image.title": "config.json"
      }
    }
  ]
}
```

#### Manifest JSON Example (Referrer / LoRA Adapter attaching to Base Model):
```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.manifest.v1+json",
  "artifactType": "application/vnd.cncf.ai.adapter.v1.peft",
  "subject": {
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "digest": "sha256:BASE_MODEL_SHA",
    "size": 1284
  },
  "config": {
    "mediaType": "application/vnd.oci.empty.v1+json",
    "digest": "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    "size": 2
  },
  "layers": [
    {
      "mediaType": "application/octet-stream",
      "digest": "sha256:ADAPTER_WEIGHTS_SHA",
      "size": 48201944,
      "annotations": {
        "org.opencontainers.image.title": "adapter_model.safetensors"
      }
    }
  ]
}
```

---

## 4. File Structures, Formatting Systems & Packaging Mechanics

### 4.1 Model Weight Formats: Comparative Analysis

| Feature / Property | GGUF (llama.cpp) | SafeTensors (Hugging Face) | Raw PyTorch (`.pt` / `.bin`) | ONNX |
| :--- | :--- | :--- | :--- | :--- |
| **Primary Use Case** | Quantized CPU / unified / edge inference (`mios-llm-light`) | High-throughput distributed GPU serving (`vLLM` / `mios-llm-heavy`) | Legacy training checkpoints | Cross-platform embedded graph execution |
| **Security / Deserialization** | **Safe:** Pure binary header + raw data blocks. Zero arbitrary execution. | **Safe:** JSON header + raw tensor byte arrays. Zero code execution. | **CRITICAL RISK:** Uses Python `pickle`; allows arbitrary code execution. | **Safe:** Protobuf binary schema. |
| **Memory Mapping (`mmap`)** | Native zero-copy `mmap`. Instant process startup. | Native zero-copy `mmap`. Eliminates duplicate RAM buffering. | Requires unpickling entire object tree into RAM. | Native `mmap` supported for external tensor data. |
| **Metadata Integration** | Self-contained: embedding dim, context len, tokenizer, chat template baked in. | Header contains shape/dtype JSON; tokenizer/configs kept in sidecar JSON. | Opaque pickled dictionary. | Protobuf model properties and graph topology. |
| **OCI Layer Packaging** | Single layer per quant file, or uncompressed tar archive. | Uncompressed tar containing `.safetensors` + `config.json` + `tokenizer.json`. | Avoid in production OCI images. | Single `.onnx` layer + external data blobs. |

### 4.2 Dataset & Prompt Formatting Standards

In modern AI CI/CD pipelines, datasets are first-class versioned code artifacts. Standard formatting encompasses three key tiers:

1. **Supervised Fine-Tuning (SFT) Format (OpenAI Chat Standard):**
   - Encoded as single-line UTF-8 JSON Lines (`.jsonl`).
   - Strict schema requiring alternating valid roles (`system`, `user`, `assistant`):
     ```json
     {"messages": [{"role": "system", "content": "You are MiOS..."}, {"role": "user", "content": "Explain UKI signing"}, {"role": "assistant", "content": "In MiOS, the UKI signing chain..."}]}
     ```
2. **Direct Preference Optimization (DPO / RLHF) Format:**
   - Evaluates pair-wise alignment and prevents model hallucination / drift against architectural laws:
     ```json
     {"input": "Where does persistent data live in bootc?", "preferred_output": "Persistent data lives in /var, which persists across bootc upgrades by default.", "non_preferred_output": "Persistent data is stored in volatile tmpfs on /var."}
     ```
3. **Dataset Provenance & MLCommons Croissant:**
   - Stored in `dataset_info.json` or Croissant JSON-LD format accompanying the dataset layer, recording:
     - `source_corpus_sha256`: Hash of source files (e.g. `manual-corpus.tsv` in MiOS).
     - `train_val_split_ratio`: Deterministic split logic (e.g., hash prefixing).
     - `license`: SPDX identifier.
     - `sanitization_receipt`: Cryptographic signature verifying zero detected credentials or PII.

### 4.3 Container Filesystem Compression & Zero-Copy Streaming
When large multi-gigabyte models are packaged into standard `.tar.gz` container layers, pulling and extracting them introduces major bottlenecks:
- **Sequential Tarball Decompression:** A 20GB tarball requires several minutes of CPU time to decompress on the node and doubles temporary disk consumption during extraction.
- **Upstream FOSS Innovations for Streaming & Zero-Copy Loading:**
  1. **Uncompressed Tar (`application/vnd.oci.image.layer.v1.tar`):**
     Omits gzip/zstd compression. When pulled, container engines write layers directly without decompression spikes. Crucially, uncompressed layers allow `mmap()` offsets to remain byte-aligned with on-disk storage.
  2. **`zstd:chunked` (OCI Chunked Compression):**
     Developed by Red Hat and container teams, `zstd:chunked` compresses tar archives in independent chunks with an embedded metadata index. Container engines (e.g., Podman with `composefs`) can fetch and extract only the requested byte ranges or mount the compressed archive directly.
  3. **`composefs` (Content-Addressable File Verification & Deduplication):**
     Natively integrated in Fedora/CentOS bootc and MiOS (`automation/93-composefs-seal.sh`). Composefs uses EROFS images referencing an underlying content-addressed store. Multiple OCI model images sharing common base weights or tokenizer configs share the exact same underlying disk blocks without file duplication.

---

## 5. Automated CI/CD Pipelines & Quality Gates

An end-to-end automated MLOps / AI CI/CD pipeline packages, gates, tests, signs, and publishes AI artifacts.

```
+----------------------------------------------------------------------------------------------------+
|                             AI ARTIFACT AUTOMATED CI/CD PIPELINE                                   |
+----------------------------------------------------------------------------------------------------+
|  [ Stage 1: Static Lint & Security Gate ]                                                          |
|  - Verify SafeTensors/GGUF magic headers (zero pickle execution)                                   |
|  - Validate JSONL schemas (OpenAI SFT/DPO specifications)                                         |
|  - Scan for secret leaks & PII (TruffleHog, Gitleaks, regex scanners)                              |
|  - Check deterministic train/validation split checksums                                            |
|                                     | (Pass)                                                       |
|                                     v                                                              |
|  [ Stage 2: Automated Eval & Quality Control Gate ]                                                |
|  - Calculate Perplexity on held-out test split                                                     |
|  - Run functional eval assertions (promptfoo / lm-evaluation-harness)                              |
|  - Verify zero regression against baseline architectural laws                                      |
|                                     | (Pass)                                                       |
|                                     v                                                              |
|  [ Stage 3: Packaging & Descriptor Closure Gate ]                                                  |
|  - Assemble OCI layout or execute `kit pack` / `buildah bud`                                       |
|  - Verify Merkle closure: every index -> manifest -> layer blob exists                             |
|  - Enforce byte-size and SHA-256 integrity                                                         |
|                                     | (Pass)                                                       |
|                                     v                                                              |
|  [ Stage 4: Cryptographic Supply Chain Gate ]                                                      |
|  - Generate CycloneDX AIBOM (base weights digest, dataset commits, hyperparameters)                |
|  - Sign OCI manifest digest with Cosign / Sigstore (keyless or HSM key)                            |
|  - Attach in-toto SLSA provenance via OCI v1.1 referrers (`cosign attest`)                         |
|                                     | (Pass)                                                       |
|                                     v                                                              |
|  [ Stage 5: Registry Promotion & Deployment ]                                                      |
|  - Push OCI manifest and referrers to OCI registry (Harbor, Forgejo, GHCR)                         |
|  - Trigger Quadlet / KServe zero-downtime rolling update via `bootc` or Podman reload              |
+----------------------------------------------------------------------------------------------------+
```

### 5.1 Pipeline Quality Gate Mechanics

#### Gate 1: Safe Deserialization & Header Verification
```bash
# Verify GGUF header magic (must start with ASCII 'GGUF')
head -c 4 models/model.gguf | grep -q "GGUF" || { echo "Invalid GGUF header"; exit 1; }

# Verify SafeTensors header (must begin with valid unsigned 64-bit little-endian integer followed by JSON)
python3 -c '
import struct, json
with open("models/model.safetensors", "rb") as f:
    header_size = struct.unpack("<Q", f.read(8))[0]
    header = json.loads(f.read(header_size).decode("utf-8"))
    assert "__metadata__" in header or len(header) > 0
'
```

#### Gate 2: Dataset Schema & Sanitization Linting
```bash
# Validate every record complies with the OpenAI chat SFT schema
python3 -c '
import json, sys
valid_roles = {"system", "user", "assistant"}
for line_no, line in enumerate(open("datasets/sft.jsonl", "r", encoding="utf-8"), 1):
    record = json.loads(line)
    messages = record.get("messages", [])
    assert len(messages) >= 2, f"Line {line_no}: messages array too short"
    for msg in messages:
        assert msg.get("role") in valid_roles, f"Line {line_no}: invalid role {msg.get(\"role\")}"
        assert len(msg.get("content", "").strip()) > 0, f"Line {line_no}: empty content"
'
```

#### Gate 3: OCI Descriptor Closure Verification
To prevent the empty-blob defect identified in `SPIKE-20260924`:
```bash
# Verify that all descriptors in index.json resolve to physical blobs on disk
python3 -c '
import json, os, hashlib, sys

layout_dir = "dist/oci-layout"
with open(os.path.join(layout_dir, "index.json")) as f:
    index = json.load(f)

for manifest_desc in index.get("manifests", []):
    algo, digest = manifest_desc["digest"].split(":")
    manifest_path = os.path.join(layout_dir, "blobs", algo, digest)
    assert os.path.isfile(manifest_path), f"Missing manifest blob: {digest}"
    
    with open(manifest_path) as mf:
        manifest = json.load(mf)
        
    for layer in manifest.get("layers", []):
        l_algo, l_digest = layer["digest"].split(":")
        blob_path = os.path.join(layout_dir, "blobs", l_algo, l_digest)
        assert os.path.isfile(blob_path), f"Dangling layer descriptor: {l_digest}"
        actual_size = os.path.getsize(blob_path)
        assert actual_size == layer["size"], f"Size mismatch for {l_digest}: expected {layer[\"size\"]}, got {actual_size}"
print("OCI Descriptor Closure Gate: PASSED (100% blobs verified)")
'
```

### 5.2 Concrete GitHub Actions / Forgejo CI Workflow Blueprint

```yaml
name: AI Artifact Packaging and Attestation Pipeline

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:

env:
  OCI_REGISTRY: ghcr.io/mios-dev/models
  MODEL_NAME: mios-coder-7b

jobs:
  lint-and-gate:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code & Datasets
        uses: actions/checkout@v4

      - name: Verify Anti-Pickle Safety & Dataset Schemas
        run: |
          python3 tools/check-ai-datasets.py --schema openai-sft datasets/sft.jsonl
          python3 tools/check-ai-datasets.py --schema openai-dpo datasets/dpo.jsonl
          python3 tools/verify-tensors.py models/

      - name: Secret & PII Scanning
        run: |
          trufflehog filesystem --fail datasets/ --no-update

  evaluate-model:
    needs: lint-and-gate
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Run Perplexity Gate
        run: |
          python3 tests/eval_perplexity.py \
            --model models/model.gguf \
            --dataset datasets/val.jsonl \
            --max-perplexity 8.5

  package-and-publish:
    needs: evaluate-model
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
      id-token: write # Required for Sigstore keyless signing

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Install Cosign & ORAS / KitOps
        uses: sigstore/cosign-installer@v3.5.0

      - name: Setup KitOps CLI
        run: |
          curl -sSL https://kitops.ml/install.sh | sh
          echo "$HOME/.local/bin" >> $GITHUB_PATH

      - name: Log in to Registry
        run: |
          echo "${{ secrets.GITHUB_TOKEN }}" | kit login ghcr.io -u "${{ github.actor }}" --password-stdin

      - name: Pack ModelKit OCI Artifact
        run: |
          kit pack . \
            -f Kitfile \
            -t ${{ env.OCI_REGISTRY }}/${{ env.MODEL_NAME }}:${{ github.ref_name }}

      - name: Push ModelKit to Registry
        run: |
          kit push ${{ env.OCI_REGISTRY }}/${{ env.MODEL_NAME }}:${{ github.ref_name }}

      - name: Generate CycloneDX AIBOM
        run: |
          python3 tools/generate-aibom.py \
            --model ${{ env.MODEL_NAME }} \
            --tag ${{ github.ref_name }} \
            --out aibom.spdx.json

      - name: Attach AIBOM & Sign Digest via Cosign
        run: |
          DIGEST=$(kit inspect ${{ env.OCI_REGISTRY }}/${{ env.MODEL_NAME }}:${{ github.ref_name }} --json | jq -r .digest)
          
          # Attach AIBOM as an OCI 1.1 Referrer
          cosign attach sbom --sbom aibom.spdx.json --type spdx ${{ env.OCI_REGISTRY }}/${{ env.MODEL_NAME }}@${DIGEST}
          
          # Cryptographically sign the artifact digest
          cosign sign --yes ${{ env.OCI_REGISTRY }}/${{ env.MODEL_NAME }}@${DIGEST}
```

---

## 6. Weighted Evaluation Matrix & Comparative Scoring

Scoring each candidate architectural pattern from **1 (Poor)** to **5 (Exceptional)** across six operational dimensions relevant to sovereign, local, immutable operating systems:

| Evaluation Criteria | Weight | Candidate A: CNCF KitOps ModelKit (`Kitfile`) | Candidate B: CNCF KServe ModelCar (Rootfs Container) | Candidate C: Ollama OCI Custom MediaTypes | Candidate D: Pure ORAS / OCI v1.1 Referrers DAG | Analysis & Observations |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Layer Granularity & Reusability** | 20% | **5** (Distinct model, dataset, code, docs layers) | **2** (Monolithic rootfs; difficult to decouple code from weights) | **4** (Clean model/template/params decomposition) | **5** (Full custom layer granularity and DAG linking) | KitOps and ORAS allow independent caching of datasets, adapters, and weights. |
| **Runtime Cold-Start & Streaming** | 25% | **4** (Unpacks into local directory; supports selective pull) | **5** (Direct bind-mount of container image filesystem; zero copy) | **5** (Direct file descriptor pass to llama.cpp; instant mmap) | **3** (Requires external client to reconstruct layout on disk) | ModelCar and Ollama excel at runtime execution without extraction overhead. |
| **Ecosystem & Tooling Ergonomics** | 20% | **4** (Intuitive `Kitfile` YAML; dedicated `kit` CLI; growing CNCF community) | **5** (Works with 100% standard `podman`, `docker`, `buildah`, `skopeo`) | **3** (Proprietary to Ollama CLI; non-standard registry interactions) | **3** (Low-level CLI; requires bespoke scripts to package assets) | ModelCar uses existing container tooling; KitOps provides the cleanest ML UX. |
| **Supply Chain & Attestation Integration** | 15% | **4** (Validates hashes; supports Cosign signing of OCI digest) | **5** (Full native support for Cosign, in-toto, Syft SBOMs) | **2** (No native integration with Sigstore / Cosign / in-toto) | **5** (Native OCI 1.1 Referrers API; purpose-built for attestations) | ModelCar and ORAS seamlessly leverage the Sigstore / Cosign ecosystem. |
| **Alignment with Immutable OS & Quadlets** | 20% | **4** (Unpacked assets mount into Quadlet volumes) | **5** (Direct integration with systemd Quadlets via `Image=` and `.volume`) | **3** (Requires running Ollama daemon inside or alongside image) | **4** (Requires puller service to stage files into `/var/lib/mios`) | ModelCar is a direct drop-in for Podman Quadlets under `usr/share/containers/systemd/`. |
| **WEIGHTED TOTAL SCORE** | **100%** | **4.20 / 5.0** | **4.45 / 5.0** | **3.60 / 5.0** | **3.90 / 5.0** | **Dual Strategy Recommended** |

---

## 7. Architectural Recommendation & Strategic Blueprint for MiOS

### 7.1 The Verdict: Adopt a Dual Tiered Strategy

Rather than forcing a single packaging format across divergent operational planes, MiOS should adopt a **Dual-Tiered Upstream FOSS AI Artifact Architecture**:

```
+----------------------------------------------------------------------------------------------------+
|                                    MIOS DUAL-TIER ARTIFACT ARCHITECTURE                            |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  TIER 1: RUNTIME INFERENCE PLANE (Candidate B: ModelCar / Quadlet Volume Mount)                     |
|  - Scope: Production model weights for `mios-llm-light` (GGUF) and `mios-llm-heavy` (SafeTensors). |
|  - Packaging: Standard container image via `Containerfile` / `buildah`. File root at `/models/`.    |
|  - Deployment: Systemd Quadlets mount the image directly using Podman image mounts.                 |
|  - Storage: Content-addressed, deduplicated, and sealed with `composefs`.                          |
|                                                                                                    |
|  TIER 2: DEVELOPMENT & DAILY TRAINING PLANE (Candidate A + D: ModelKit / OCI 1.1 Referrers)       |
|  - Scope: Daily fine-tuning datasets (`sft.jsonl`, `dpo.jsonl`), LoRA adapters, evaluation suites. |
|  - Packaging: Declarative `Kitfile` and OCI v1.1 artifact layout.                                  |
|  - Governance: Gated by `[artifacts.daily]` SSOT and automated Merkle descriptor closure.          |
|  - Supply Chain: Signed via `cosign`, with in-toto SLSA attestations and CycloneDX AIBOM.          |
|                                                                                                    |
+----------------------------------------------------------------------------------------------------+
```

### 7.2 Actionable Implementation Steps for MiOS

1. **Implement `mios-artifact-validate` Static Rust Binary (Tools Native):**
   - Per Law 14 (TARGET-LANGUAGES), compile a high-performance Rust static binary in `tools/native/` that verifies OCI descriptor closure, validates JSONL schemas against OpenAI strict JSON formats, verifies GGUF/SafeTensors magic headers, and scans for credential leaks.
   - Embed this validator as a mandatory gate in `tools/sync-generated.sh` and CI/CD runs.
2. **Standardize Quadlet ModelCar Mounting:**
   - Configure `usr/share/containers/systemd/mios-llm-light.container` and `mios-llm-heavy.container` to support mounting local OCI ModelCar images directly via Podman named volumes or image mounts, eliminating runtime model downloads.
3. **Harmonize Daily Artifact Output with OCI v1.1 Specifications:**
   - Update `[artifacts.daily]` in `usr/share/mios/mios.toml` to emit standardized OCI v1.1 layout archives and declarative `Kitfile` manifests alongside the existing SFT and DPO JSONL files.
4. **Draft Architecture Decision Record (ADR):**
   - Record this dual-tier architecture as ADR 0027 (`usr/share/doc/mios/adr/0027-dual-tier-oci-ai-artifacts.md`), establishing OCI ModelCars for inference and OCI 1.1 ModelKits for training datasets and epistemic learning (`/learn`).

---

## 8. What Remains Unverified & Follow-Up Spikes

1. **Podman Image Mount Performance under High Concurrency:**
   - *Question:* Does running multiple concurrent instances of `llama.cpp` or `vLLM` against a shared `podman image mount` or Quadlet volume introduce VFS lock contention compared to a direct host bind mount?
   - *Follow-up:* Conduct an I/O latency benchmark measuring Time-To-First-Token (TTFT) across direct bind mounts vs container image mounts on Fedora bootc with composefs enabled.
2. **Native OCI 1.1 Referrers API Support in Local Forgejo:**
   - *Question:* Does the local embedded Forgejo container instance fully support the OCI Distribution Specification v1.1 Referrers API (`/v2/<name>/referrers/<digest>`), or does it require fallback to ORAS tag-based schema referrers?
   - *Follow-up:* Probe the local Forgejo registry endpoint using `oras discover` and `cosign referrers` to verify native reverse-DAG query capability.
