<!-- AI-hint: Dual-tier OCI AI artifact architecture: standard OCI ModelCars for resident inference lanes via Podman Quadlet image mounts and composefs; CNCF KitOps ModelKits for development, training corpora, SFT/DPO datasets, and LoRA adapters. Read before packaging or deploying any AI model or fine-tuning dataset. -->
<!-- AI-related: usr/share/doc/mios/adr/0027-dual-tier-oci-ai-artifacts.md, ROADMAP.md (task_3224..task_3227), usr/share/mios/mios.toml [artifacts.daily], [finetune.micro], usr/share/containers/systemd/mios-llm-light.container, usr/lib/bootc/bound-images.d/ -->
---
adr: 0027
title: Dual-tier OCI AI artifact architecture for resident inference and daily training corpora
status: accepted
date: 2026-09-26
deciders: [operator, ai-pair]
tags: [oci, ai, artifacts, modelkit, modelcar, bootc, quadlets, rust, gates]
laws: [1, 3, 5, 12, 14, 16]
ssot_keys: [finetune.micro, llamacpp, artifacts.daily, image.sidecars]
related_ws: [WS-DEPRED, WS-SBOM]
supersedes: []
superseded_by: []
---

# ADR-0027: Dual-tier OCI AI artifact architecture for resident inference and daily training corpora

## Status

accepted — 2026-09-26

Fulfills and coordinates Roadmap items `task_3224` through `task_3227`. Ratifies the consensus reached in technical evaluation `docs/research/tech-eval-oci-ai-artifacts-and-pipelines.md` and aligns with Law 1 (FHS), Law 3 (BOUND-IMAGES), Law 5 (UNIFIED-AI-REDIRECTS), Law 12 (BAKE-NOT-FETCH), Law 14 (TARGET-LANGUAGES), and Law 16 (ONE-TEMPLATE-PER-TYPE).

## Context

MiOS is an immutable, bootc/OCI-shaped Fedora workstation that is also a local, self-replicating agentic AI OS. AI inference and fine-tuning require managing two distinct categories of data assets:
1. **Resident Inference Weights:** Multi-gigabyte foundation model weights (`mios-llm-light` running GGUF on `llama-swap`; `mios-llm-heavy` running SafeTensors on `vLLM`).
2. **Development & Training Corpora:** Daily Supervised Fine-Tuning (SFT) and Direct Preference Optimization (DPO) datasets, LoRA adapters, evaluation suites, and system prompts produced by `usr/libexec/mios/mios-finetune-dataset` and `[artifacts.daily]`.

Prior to this decision, AI assets faced three architectural tensions:
- Downloading multi-gigabyte models at pod/container startup via ad-hoc scripts or curl fetchers violates Law 12 (BAKE-NOT-FETCH) and causes cold-start latency.
- Baking raw model weights directly into the base OS root filesystem bloats the OS image by tens of gigabytes, making fast `bootc upgrade` and `rollback` operations impractical.
- Unstandardized archive packaging risks emitting index-only archives lacking physical blobs (`SPIKE-20260924`) and lacks cryptographic provenance tying fine-tuned adapters back to source training datasets.

## Decision

MiOS adopts a **Dual-Tiered OCI AI Artifact Architecture**:

### 1. Tier 1: Runtime Inference Plane (CNCF KServe / Bootc ModelCar)
- Production inference model weights are packaged as standard OCI container filesystem images (`FROM scratch`, `COPY ... /models/`).
- Declared as pre-bound container images under `/usr/lib/bootc/bound-images.d/` per Law 3 (BOUND-IMAGES) and resolved at image bake time per ADR-0003 with zero hand-pinned digests in `mios.toml`.
- Delivered to runtime inference engines via native Podman Quadlet image volume mounts:
  ```ini
  Mount=type=image,source=mios-micro.image,destination=/models-micro
  ```
- Stored and executed directly from the container storage graph driver without intermediate tar extraction, deduplicated and integrity-sealed via `composefs` and zero-copy `mmap`.

### 2. Tier 2: Development & Daily Training Plane (CNCF KitOps ModelKit / OCI 1.1 Referrers)
- Training datasets (`sft.jsonl`, `dpo.jsonl`), LoRA delta adapters, manual corpus grounding documentation, and training driver scripts are packaged as CNCF KitOps ModelKits adhering to the `Kitfile` v1.0.0 specification.
- A canonical `Kitfile` template lives at `usr/share/mios/templates/kitfile` (scaffolded as `<type>` = `kitfile`) per Law 16 (ONE-TEMPLATE-PER-TYPE), populated dynamically from the `[artifacts.daily]` SSOT table.
- Discrete OCI layers are generated with standard media types (`application/vnd.kitops.modelkit.model.v1+tar`, `...dataset.v1+tar`, `...code.v1+tar`), enabling selective layer pulling without downloading unchanged base model weights.
- Cryptographic provenance is enforced using Sigstore/Cosign: signatures and CycloneDX AIBOM attestations are attached to artifact digests via the OCI Distribution v1.1 Referrers API (`subject` link).

### 3. Standing Verification Gate (Law 14 Rust Static Binary)
- A Rust static verification gate (`mios-gate artifact` in `tools/native/` and `src/mios-rs/`) validates all emitted AI artifacts prior to publication.
- Verification checks:
  - **Descriptor Closure:** 100% of referenced descriptors in `index.json` and manifests resolve to physical blobs with matching SHA256 hashes and byte lengths.
  - **Safe Deserialization:** Zero Python pickle deserialization (`.pt`/`.bin` rejected); enforces ASCII `GGUF` magic and SafeTensors JSON header validation.
  - **OpenAI Schema Conformance:** Validates UTF-8 JSON Lines against OpenAI chat SFT and preference DPO schemas, scrubbing credentials and private identifiers.

## Rationale

1. **Law 3 & Law 12 Compliance:** ModelCars integrate natively with `bootc` bound-images and Podman Quadlets, ensuring immediate offline availability upon boot without downloading weights over the network.
2. **Zero Storage Duplication:** Native Podman image mounts (`:image,ro`) combined with `composefs` allow inference engines to memory-map model files directly from content-addressed storage without extracting duplicate gigabytes to `/var`.
3. **Decoupled Training Cadence:** KitOps ModelKits allow daily dataset drops (`[artifacts.daily]`) and LoRA adapters to be pushed and pulled independently from multi-gigabyte foundation model weights.
4. **Law 14 Static Verification:** Implementing the artifact validator in Rust provides sub-second verification in local CI/CD loops and pre-commit hooks without invoking Python ML dependencies.

## Alternatives Considered

- **Pure ModelKit across both planes:** Rejected because standard container runtimes (Podman, crun) cannot natively mount ModelKit custom media layers without an intermediary daemon or custom extraction step, which introduces cold-start latency.
- **Pure ModelCar for all assets:** Rejected because packaging small daily SFT/DPO datasets and LoRA adapters inside monolithic container filesystem layers prevents selective layer pulling and decouples datasets from ModelKit/Croissant ML metadata standards.
- **Direct rootfs baking:** Rejected because baking 10GB–50GB of weights directly into `/usr` bloats the core OS image and slows bootc upgrades.

## Consequences

- Quadlet templates (`usr/share/containers/systemd/mios-llm-light.container`) gain native image volume mounts pointing to local ModelCar images.
- A canonical `Kitfile` template is added to `usr/share/mios/templates/kitfile` and registered in `[templates.kitfile]`.
- `mios-gate` expands with the `artifact` subcommand in `src/mios-rs/mios-gate/`.
- Daily artifacts emitted under `[artifacts.daily]` conform to the dual-tier standard with verified descriptor closure.

## Implementation

1. `usr/share/doc/mios/adr/0027-dual-tier-oci-ai-artifacts.md` (this record).
2. `usr/share/mios/templates/kitfile` (canonical Law 16 template).
3. `src/mios-rs/mios-gate/src/artifact.rs` (`mios-gate artifact` validator).
4. `[images.mios-micro]` in `mios.toml`, generated as `mios-micro.image` (ModelCar bound image: the binder links every `.image` unit into `bound-images.d`).
5. `usr/share/containers/systemd/mios-llm-light.container` (Quadlet image volume mount).

## References

- `docs/research/tech-eval-oci-ai-artifacts-and-pipelines.md`
- `docs/research/spike-artifact-publisher-oci-and-training-data.md`
- OCI Image Specification v1.1.1 (`opencontainers/image-spec`)
- CNCF KitOps ModelKit Specification v1.0.0 (`kitops.ml`)
- CNCF KServe ModelCar Pattern (`kserve.github.io`)
- ADR-0003: SBOM-not-hardcode: digests are build-resolved provenance
- ADR-0021: Rust static binary consolidation
