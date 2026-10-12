<!-- AI-hint: Consolidates authoritative documentation links and technical specifications for OpenAI APIs, vector stores, and MiOS-specific knowledge base construction to ensure agent compliance with upstream standards.
     AI-related: /etc/mios/ai, /usr/share/mios/flatpak-list, mios-dev, mios-build-local, mios-overlay, mios-sysext-pack, mios-ci, mios-bootstrap, mios-ai, mios-hardening -->
# usr/share/doc/mios/reference/sources.md -- References, Sub-Knowledge, and Iteration Pointers

This file consolidates every authoritative source consulted to build this
knowledge base, plus pointers for further iteration on OpenAI API
compliance and 'MiOS' upstream technologies. Every claim in
`usr/share/doc/mios/**/*.md` should trace to one of these sources.

---

## 1. OpenAI Platform -- API Compliance Anchors

The 'MiOS' KB is authored against these specifications. Each link is the
*current* (2026) reference -- re-fetch periodically; OpenAI iterates fast.

### 1.1 Responses API (recommended for new projects)

- API reference (create): https://platform.openai.com/docs/api-reference/responses/create
- API reference (get): https://platform.openai.com/docs/api-reference/responses/get
- Migration guide (Chat Completions → Responses): https://developers.openai.com/api/docs/guides/migrate-to-responses
- Key fields: `model`, `instructions` (system/developer), `input` (string OR array of typed items), `tools`, `text.format` (structured outputs), `previous_response_id` (multi-turn -- note that `instructions` are NOT carried across), `store` (default true)

### 1.2 Chat Completions API (universal)

- API reference: https://platform.openai.com/docs/api-reference/chat/create
- Streaming: `stream: true` returns SSE chunks with `delta` shape
- Tools: `tools[].type = "function"`, then `function: {name, description, parameters, strict?}`
- Structured outputs: `response_format: {type: "json_schema", json_schema: {name, schema, strict}}`
- This is the form supported by **every** OpenAI-compatible local runtime (Ollama, vLLM, LM Studio, llama.cpp server, LiteLLM, OpenRouter)

### 1.3 Vector Stores / File Search

- Create vector store file: https://developers.openai.com/api/reference/resources/vector_stores/subresources/files/methods/create
- Assistants File Search guide: https://developers.openai.com/api/docs/assistants/tools/file-search
- Files API alternatives & limits: https://fast.io/resources/openai-files-api-alternative/
- Limits: per-file ≤ 512 MB; vector store ≤ 10,000 files (≤ 100,000,000 for stores created Nov 2025+); first GB free, then $0.10/GB/day
- Chunking strategies:
  - `{type: "auto"}` defaults: `max_chunk_size_tokens=800`, `chunk_overlap_tokens=400`
  - `{type: "static", max_chunk_size_tokens, chunk_overlap_tokens}` with `max ∈ [100, 4096]`, `overlap ≤ max/2`
- Attributes map: ≤ 16 keys, key ≤ 64 chars, string value ≤ 512 chars (numbers and booleans also accepted)
- Client moved from `client.beta.vector_stores` to `client.vector_stores` -- older code samples may show the `beta` path

### 1.4 Function Calling (strict mode)

- Guide: https://platform.openai.com/docs/guides/function-calling
- Strict-mode rules: `strict: true` requires (a) every property listed in `required`, (b) `additionalProperties: false` on every nested object, (c) optional fields modeled as `["type", "null"]` unions (not "missing key")
- Structured Outputs JSON Schema reference: https://deepwiki.com/openai/openai-dotnet/5.3-structured-outputs

### 1.5 Batch API

- Guide: https://platform.openai.com/docs/guides/batch
- Create batch: https://platform.openai.com/docs/api-reference/batch/create
- Input format: JSONL with `{custom_id, method: "POST", url: "/v1/responses"|"/v1/chat/completions"|"/v1/embeddings", body}`
- Limits: ≤ 50,000 requests, ≤ 200 MB per file
- Cost: 50% discount on completion within 24h
- Upload purpose: `"batch"` (not `"fine-tune"`)

### 1.6 Evals API

- Working with evals: https://developers.openai.com/api/docs/guides/evals
- Tutorial: https://www.leanware.co/insights/openai-evals-api-guide
- Definition shape: `POST /v1/evals` with `data_source_config: {type: "custom", item_schema, include_sample_schema: true}` and `testing_criteria: [...]`
- Grader types: `string_check`, `text_similarity`, `label_model`, `score_model`, `python`
- Run shape: `POST /v1/evals/{id}/runs` with `data_source: {type: "responses"|"completions"|"jsonl", source, input_messages, model}`

### 1.7 Fine-tuning -- SFT

- JSONL format primer: https://resources.codefriends.net/en/ai/fine-tuning/basics/chapter-2/jsonl-for-training
- Per-line shape: `{"messages": [{"role": "system"|"user"|"assistant"|"tool", "content": "..."}]}`
- Tool calls supported: `assistant` messages may include `tool_calls`, `tool` messages reference `tool_call_id`
- Minimum 10 examples, recommend ≥ 50 for stable behavior
- Upload purpose: `"fine-tune"`

### 1.8 Fine-tuning -- DPO (Direct Preference Optimization)

- Guide: https://platform.openai.com/docs/guides/direct-preference-optimization
- Cookbook: https://cookbook.openai.com/examples/fine_tuning_direct_preference_optimization_guide
- Azure Foundry equivalent: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/fine-tuning-direct-preference-optimization
- Per-line shape:
  ```json
  {"input": {"messages": [...], "tools": [], "parallel_tool_calls": true},
   "preferred_output": [{"role": "assistant", "content": "..."}],
   "non_preferred_output": [{"role": "assistant", "content": "..."}]}
  ```
- Exactly two completions per line; recommended after an SFT pass

### 1.9 Embeddings

- Models: `text-embedding-3-small` (default 1536 dims, configurable down to 512), `text-embedding-3-large` (default 3072, configurable down to 256)
- Both have 8191-token context (`cl100k_base` tokenizer)
- Recommended chunk sizing: 400-800 tokens for general docs, 200-500 for fact-dense reference material
- Reference: https://vectorize.io/blog/openai-text-embedding-3-embedding-models-first-look

### 1.10 MCP Tool (Responses API only)

- Tool object shape:
  ```json
  {"type": "mcp",
   "server_label": "...",
   "server_url": "https://...",
   "require_approval": "never" | "always",
   "allowed_tools": [...],
   "headers": {"Authorization": "Bearer ..."}}
  ```
- Security: OpenAI retains only schema/domain/subdomains of `server_url` between calls; auth headers must be re-sent every request
- Not available via Chat Completions

### 1.11 Prompt engineering & reasoning effort

- Prompt engineering guide: https://developers.openai.com/api/docs/guides/prompt-engineering
- XML-style structuring works well; markdown supported; multi-section prompts (`<role>`, `<task>`, `<output_contract>`) recommended
- For o-series reasoning models: `reasoning.effort: "low" | "medium" | "high"` and `reasoning.summary` are accepted in Responses API

---

## 2. 'MiOS' Repository -- File-Level Sources

Every chunk in `usr/share/doc/mios/*.md` (under this KB) traces to one or
more of these 'MiOS' files. Re-fetch them via the `mios_build_kb_refresh`
tool to refresh the KB.

### 2.1 Documentation files (root)

- `README.md` -- https://github.com/mios-dev/MiOS/blob/main/README.md
- `usr/share/mios/ai/INDEX.md` -- https://github.com/mios-dev/MiOS/blob/main/INDEX.md (single source of truth for architectural laws)
- `usr/share/doc/mios/concepts/architecture.md` -- https://github.com/mios-dev/MiOS/blob/main/ARCHITECTURE.md
- `usr/share/doc/mios/guides/engineering.md` -- https://github.com/mios-dev/MiOS/blob/main/ENGINEERING.md
- `SECURITY.md` -- https://github.com/mios-dev/MiOS/blob/main/SECURITY.md
- `usr/share/doc/mios/guides/self-build.md` -- https://github.com/mios-dev/MiOS/blob/main/SELF-BUILD.md
- `usr/share/doc/mios/guides/deploy.md` -- https://github.com/mios-dev/MiOS/blob/main/DEPLOY.md
- `CONTRIBUTING.md` -- https://github.com/mios-dev/MiOS/blob/main/CONTRIBUTING.md
- `usr/share/doc/mios/reference/licenses.md` -- https://github.com/mios-dev/MiOS/blob/main/LICENSES.md
- `LICENSE` -- https://github.com/mios-dev/MiOS/blob/main/LICENSE (Apache-2.0)
- `VERSION` -- https://github.com/mios-dev/MiOS/blob/main/VERSION

### 2.2 Agent-facing documentation

- `CLAUDE.md` -- https://github.com/mios-dev/MiOS/blob/main/CLAUDE.md (un-labeled OpenAI-API pointer; filename for tooling discovery only)
- `AGENTS.md` -- https://github.com/mios-dev/MiOS/blob/main/AGENTS.md (generic agents.md standard)
- `GEMINI.md` -- https://github.com/mios-dev/MiOS/blob/main/GEMINI.md
- `system-prompt.md` -- https://github.com/mios-dev/MiOS/blob/main/system-prompt.md (canonical repo-root pointer; matches override-layer naming)
- `usr/share/mios/ai/system.md` -- canonical agent prompt (deployed into the image)
- `usr/share/mios/ai/v1/models.json` -- `/v1/models`-shaped catalog
- `usr/share/mios/ai/v1/mcp.json` -- MCP server registry

### 2.3 Build infrastructure

- `Containerfile` -- https://github.com/mios-dev/MiOS/blob/main/Containerfile (single-stage + `ctx` scratch context, final RUN is `bootc container lint`)
- `Justfile` -- https://github.com/mios-dev/MiOS/blob/main/Justfile (Linux build orchestrator)
- `build-mios.sh` -- https://github.com/mios-dev/MiOS/blob/main/build-mios.sh
- `mios-build-local.ps1` -- https://github.com/mios-dev/MiOS/blob/main/mios-build-local.ps1 (Windows 5-phase orchestrator)
- `preflight.ps1` -- https://github.com/mios-dev/MiOS/blob/main/preflight.ps1
- `push-to-github.ps1` -- https://github.com/mios-dev/MiOS/blob/main/push-to-github.ps1
- `Get-MiOS.ps1` -- https://github.com/mios-dev/MiOS/blob/main/Get-MiOS.ps1
- `install.ps1` / `install.sh` (owned by mios-bootstrap) -- https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/install.ps1 / https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/install.sh
- `image-versions.yml` -- https://github.com/mios-dev/MiOS/blob/main/image-versions.yml (Renovate-tracked digests)
- `renovate.json` -- https://github.com/mios-dev/MiOS/blob/main/renovate.json
- `automation/` -- https://github.com/mios-dev/MiOS/tree/main/automation (~48 numbered phase scripts plus `build.sh` and `lib/{common,packages,masking}.sh`)

### 2.4 LLM ingestion entrypoints

- `llms.txt` -- https://github.com/mios-dev/MiOS/blob/main/llms.txt (lightweight AI ingestion index, llmstxt.org standard)
- `llms-full.txt` -- https://github.com/mios-dev/MiOS/blob/main/llms-full.txt (full-content variant)
- `tools/ascii-sweep.py` -- https://github.com/mios-dev/MiOS/blob/main/tools/ascii-sweep.py (typography + emoji scrubber across `git ls-files`; the AI-artifact sanitization helper)

### 2.5 Repository layout (FHS overlay)

The repo root **is** the system root. These directories ship 1:1 into
the deployed image via the `ctx` scratch stage and the
`automation/01-system-files-overlay.sh` overlay step:

- `usr/` -- read-only system content (binaries, libraries, vendor configs, kargs.d, systemd units, AI surface, SELinux modules)
- `etc/` -- host-overridable configs (Quadlets, repo files, AI overrides)
- `home/` -- bootstrap territory (per-user homes staged in Phase-3)
- `srv/` -- data served by the system (AI model weights, Ceph data -- declared via `usr/lib/tmpfiles.d/`)
- `v1/` -- versioned API surface artifacts
- `config/` -- build-time configs (notably `config/artifacts/{bib,iso,qcow2,vhdx,wsl2}.toml` for BIB)
- `tools/` -- build helpers (`preflight.sh`, `mios-overlay.sh`, `mios-sysext-pack.sh`, `flight-control.sh`, `init-user-space.sh`, `log-to-bootstrap.sh`); sourced helpers in `tools/lib/` (`userenv.sh`)

### 2.6 CI

- `.github/workflows/mios-ci.yml` -- https://github.com/mios-dev/MiOS/blob/main/.github/workflows/mios-ci.yml (build → rechunk on tag → cosign keyless sign → push to GHCR; lint enforced via shellcheck SC2038, hadolint, TOML validation)

### 2.7 Bootstrap repo (separate)

- `mios-bootstrap` -- https://github.com/mios-dev/mios-bootstrap (user-facing installer; owns Phase-0 preflight + identity, Phase-1 Total Root Merge, Phase-4 reboot)

---

## 3. Upstream Technologies

### 3.1 bootc (CNCF Sandbox)

- Project: https://github.com/bootc-dev/bootc
- Docs: https://bootc-dev.github.io/bootc/ (current canonical) and https://bootc.dev/
- Kernel arguments format: https://bootc.dev/bootc/building/kernel-arguments.html (flat `kargs = [...]` TOML, no section header, no `delete` sub-key)
- Install flow: https://bootc.dev/bootc/bootc-install.html
- Releases: https://github.com/bootc-dev/bootc/releases
- All Systems Go 2024 talk (trusted boot chain): https://cfp.all-systems-go.io/all-systems-go-2024/talk/HVEZQQ/
- Key commands: `bootc status [--format=json]`, `bootc upgrade [--apply]`, `bootc switch <ref>`, `bootc rollback`, `bootc kargs edit`, `bootc kargs --delete`, `bootc install to-disk`, `bootc install to-filesystem`, `bootc container lint`

### 3.2 ostree / libostree

- Project: https://github.com/ostreedev/ostree
- Docs: https://ostreedev.github.io/ostree/
- Concepts: content-addressed object store, refs, deployments, `/sysroot` physical root, `/var` mutable subvolume, `/etc` 3-way merge
- bootc currently uses ostree as backend; composefs is the migration path

### 3.3 composefs

- Project: https://github.com/containers/composefs (alt: https://github.com/composefs/composefs)
- v1.0.0 release: https://github.com/composefs/composefs/releases/tag/v1.0.0
- Stack: overlayfs + EROFS + fs-verity → verifiable read-only mount with content-addressed dedup
- 'MiOS' enables it via `usr/lib/ostree/prepare-root.conf`

### 3.4 Universal Blue / ucore / ucore-hci

- Org: https://github.com/ublue-os
- ucore: https://github.com/ublue-os/ucore (Fedora CoreOS base with batteries; multi-arch since 2025-11-08; ZFS in base since 2025-06-12; tags `:stable`, `:testing`, `:stable-nvidia`, `:stable-nvidia-lts`)
- ucore-hci: hyperconverged-infrastructure variant (libvirt/KVM, QEMU, VFIO-PCI, virtiofs added on top of ucore) -- **MiOS's base image**
- ccos (CentOS-based CoreOS-style bootc image): https://github.com/ublue-os/ccos
- Sibling images:
  - Bluefin (developer workstation, GNOME): https://github.com/ublue-os/bluefin
  - Aurora (KDE): https://github.com/ublue-os/aurora
  - Bazzite (gaming/handheld): https://github.com/ublue-os/bazzite

### 3.5 Fedora bootc

- Base images: `quay.io/fedora/fedora-bootc` (tags `42`, `43`, `rawhide`)
- Building blocks: https://gitlab.com/fedora/bootc/base-images
- Anaconda bootc kickstart: https://fedoramagazine.org/introducing-the-new-bootc-kickstart-command-in-anaconda/
- Fedora Magazine bootc desktop guide: https://fedoramagazine.org/building-your-own-atomic-bootc-desktop/
- RHEL image mode (sibling): https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/using_image_mode_for_rhel_to_build_deploy_and_manage_operating_systems/index
- RHEL FIPS in bootc: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/using_image_mode_for_rhel_to_build_deploy_and_manage_operating_systems/enabling-the-fips-mode-while-building-a-bootc-image

### 3.6 dnf5

- Project: https://github.com/rpm-software-management/dnf5
- Docs: https://dnf5.readthedocs.io/
- Critical knob: `install_weak_deps=False` (underscore -- dnf5 spelling); `install_weakdeps` is dnf4 and silently ignored by dnf5

### 3.7 Podman / buildah / skopeo

- Podman: https://github.com/containers/podman ; docs https://docs.podman.io/
- buildah: https://github.com/containers/buildah
- skopeo: https://github.com/containers/skopeo
- Quadlet (systemd integration): https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html
- Avoid `--squash-all` for bootc images (strips OCI metadata bootc needs)

### 3.8 bootc-image-builder (BIB)

- Project: https://github.com/osbuild/bootc-image-builder
- Docs: https://osbuild.org/docs/bootc/
- Image: `quay.io/centos-bootc/bootc-image-builder:latest`
- Output types: `ami`, `anaconda-iso`, `gce`, `iso`, `qcow2`, `raw`, `vhd`, `vmdk`, `wsl2`
- Config TOML sections: `[customizations.installer.kickstart]`, `[customizations.iso]`, `[customizations.user]`, `[customizations.kernel]` (note: `user` and `installer.kickstart` are mutually exclusive)
- Successor under evaluation: https://github.com/osbuild/image-builder-cli (first-class SBOM + cross-arch)

### 3.9 rechunk

- Project: https://github.com/hhd-dev/rechunk
- Tool: `bootc-base-imagectl rechunk --max-layers 67 <src> <dst>`
- Purpose: optimize OCI layer structure for 5-10× smaller `bootc upgrade` deltas

### 3.10 Cosign / Sigstore

- Project: https://github.com/sigstore/cosign
- Keyless signing flow: ephemeral keypair → OIDC token → Fulcio cert → image signed → Rekor transparency log entry → signature pushed to OCI registry as companion artifact
- Verification example: https://secure-pipelines.com/ci-cd-security/signing-verifying-container-images-sigstore-cosign/
- What is Sigstore: https://sbomify.com/2024/08/12/what-is-sigstore/
- Attestation walkthrough: https://www.augmentedmind.de/2025/03/02/docker-image-signing-with-cosign/
- Attestation predicate types: `slsaprovenance`, `slsaprovenance1`, `spdxjson`, `cyclonedx`, `vuln`, `openvex`

### 3.11 syft (SBOM)

- Project: https://github.com/anchore/syft
- Image: `anchore/syft:latest`
- 'MiOS' uses it via `automation/90-generate-sbom.sh` to emit CycloneDX-JSON

### 3.12 GitHub Container Registry (GHCR)

- Docs: https://docs.github.com/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- Auth: GitHub PAT or `GITHUB_TOKEN` with `packages: write`
- 'MiOS' retention: latest signed digest + last 5 release digests protected; untagged pruned at 90 days

### 3.13 NVIDIA on Fedora bootc

- akmod: https://rpmfusion.org/Packaging/KernelModules/Akmods
- nvidia-container-toolkit: https://github.com/NVIDIA/nvidia-container-toolkit
- CDI generation: `nvidia-ctk cdi generate`
- ucore variants: `:stable-nvidia` (open kernel modules, Turing+) and `:stable-nvidia-lts` (proprietary 580 LTS, supports Maxwell/Pascal)

### 3.14 Container Device Interface (CDI)

- Spec: https://github.com/cncf-tags/container-device-interface
- Spec output: `/var/run/cdi/`
- Admin overrides: `/etc/cdi/`
- Universal layer for NVIDIA, AMD ROCm/KFD, Intel iGPU passthrough into containers

### 3.15 llama.cpp + llama-swap (the 'MiOS' canonical local LLM lane)

- Project: https://github.com/ggerganov/llama.cpp -- swap proxy: https://github.com/mostlygeek/llama-swap
- API surfaces (OpenAI-compatible): `/v1/models`, `/v1/chat/completions` (SSE + tools), `/v1/embeddings`, `/v1/completions`
- 'MiOS' canonical endpoint: `MIOS_AI_ENDPOINT` (the `mios-llm-light` lane, port key `llm_light`, fronted by the Hermes gateway, port key `hermes`)
- Backends: GGUF models auto-swapped by llama-swap; heavy GPU lanes add vLLM / SGLang

### 3.16 Other local OpenAI-compatible runtimes (for Day-0 portability)

- Ollama: https://github.com/ollama/ollama (OpenAI-compatible at `/v1`; the retired Ollama lane -- an API-compat reference only, not a MiOS lane)
- vLLM: https://github.com/vllm-project/vllm (`vllm serve <model>` exposes `http://localhost:8000/v1`)
- LM Studio: https://lmstudio.ai/ (OpenAI-compatible at `http://localhost:1234/v1`)
- llama.cpp server: https://github.com/ggerganov/llama.cpp (`./llama-server`)
- LiteLLM: https://github.com/BerriAI/litellm (proxy translating between dialects, including Responses ↔ Chat Completions)
- OpenRouter: https://openrouter.ai/ (cloud aggregator, OpenAI-compatible)

### 3.17 MCP (Model Context Protocol)

- Spec: https://modelcontextprotocol.io/
- SDKs: https://github.com/modelcontextprotocol
- OpenAI Responses MCP integration: see §1.10

### 3.18 Looking Glass / KVMFR

- Looking Glass: https://looking-glass.io/
- KVMFR shared-memory module: built in-image via `automation/52-bake-kvmfr.sh`
- Looking Glass B7 client: built in-image via `automation/53-bake-lookingglass-client.sh`

### 3.19 SecureBlue (security audit framework)

- Project: https://github.com/secureblue/secureblue
- Fedora hardening guidelines: https://docs.fedoraproject.org/en-US/quick-docs/securing-fedora/
- Kernel admin guide (kargs reference): https://www.kernel.org/doc/html/latest/admin-guide/kernel-parameters.html
- Sysctl reference: https://www.kernel.org/doc/Documentation/sysctl/

### 3.20 Defense-in-depth components

- SELinux: https://github.com/SELinuxProject/selinux
- firewalld: https://firewalld.org/
- CrowdSec: https://www.crowdsec.net/ ; project https://github.com/crowdsecurity/crowdsec
- fapolicyd: https://github.com/linux-application-whitelisting/fapolicyd
- USBGuard: https://usbguard.github.io/
- greenboot (operational health): https://github.com/fedora-iot/greenboot

### 3.21 Cluster & remote access

- K3s: https://k3s.io/ ; https://github.com/k3s-io/k3s
- Cockpit: https://cockpit-project.org/
- Ceph: https://ceph.io/ ; cephadm https://docs.ceph.com/en/latest/cephadm/
- libvirt: https://libvirt.org/ ; QEMU: https://www.qemu.org/

### 3.22 FHS 3.0 specification

- Spec: https://refspecs.linuxfoundation.org/FHS_3.0/
- Key intent: `/usr` "shareable, read-only" (composefs/ostree enforce this at the kernel level), `/etc` host-specific config (3-way merged on bootc upgrade), `/var` mutable+persistent (never touched by upgrade), `/srv` data served by the system

### 3.23 Related immutable/atomic distros (comparison context)

- Fedora Silverblue / Kinoite (rpm-ostree): https://fedoraproject.org/silverblue/
- CoreOS Layering / rpm-ostree: https://github.com/coreos/rpm-ostree
- NixOS: https://nixos.org/ (declarative, not OCI)
- Talos: https://www.talos.dev/ (Kubernetes-only, API-driven)
- Flatcar: https://www.flatcar.org/ (Container Linux successor)
- Vanilla OS: https://vanillaos.org/

### 3.24 llms.txt standard (LLM ingestion entrypoint)

- Spec: https://llmstxt.org/ (Answer.AI proposal -- `/llms.txt` for LLM-friendly site indexing, `/llms-full.txt` for full-content variant). 'MiOS' publishes both at the repo root.

---

## 4. Sub-Knowledge -- For Iteration

### 4.1 Things this KB does NOT yet cover (next ingestion targets)

When you re-run the KB refresh, prioritize these files (they were
referenced but not yet scraped to chunk-level detail):

- `usr/share/mios/PACKAGES.md` -- actual fenced-block package contents
- `usr/share/mios/ai/system.md` -- canonical agent prompt
- `usr/share/mios/ai/v1/models.json` -- actual model catalog
- `usr/share/mios/ai/v1/mcp.json` -- actual MCP server registry
- `automation/build.sh` -- orchestrator entrypoint
- `automation/lib/{common,packages,masking}.sh` -- shared lib functions
- All `automation/[0-9][0-9]-*.sh` scripts (~48 files)
- All `etc/containers/systemd/mios-*.container` Quadlet files
- All `usr/lib/bootc/kargs.d/*.toml` (`00-mios.toml` is the entry point; later priority files exist)
- `usr/lib/sysctl.d/99-mios-hardening.conf`
- `usr/lib/ostree/prepare-root.conf`
- `usr/lib/tmpfiles.d/mios*.conf` (notably `mios-gpu.conf`, `mios.conf`)
- `usr/share/selinux/packages/mios/*.te`
- `config/artifacts/{bib,iso,qcow2,vhdx,wsl2}.toml` -- actual BIB configs
- `etc/fapolicyd/fapolicyd.rules` -- actual fapolicyd policy
- `.github/workflows/mios-ci.yml` -- actual CI pipeline

### 4.2 Open OpenAI API surfaces to track

- Realtime API (voice/streaming): https://platform.openai.com/docs/guides/realtime
- Image generation (`/v1/images/generations`): https://platform.openai.com/docs/api-reference/images
- Audio (TTS, STT): https://platform.openai.com/docs/guides/audio
- Computer Use / browser tools (Responses): if 'MiOS' adds a UI-automation surface, integrate here
- Reasoning models (`o3`, `o4-mini`, future): `reasoning.effort`, `reasoning.summary` in Responses API

### 4.3 OpenAI API surfaces NOT supported by typical local runtimes

If your KB consumer is local-only (Ollama, vLLM, LM Studio,
llama.cpp), these surfaces require either OpenAI cloud, Azure OpenAI, or
a translation proxy (LiteLLM):

- `/v1/responses` -- Responses API
- `/v1/vector_stores` -- Vector Stores
- `/v1/batches` -- Batch API
- `/v1/evals` -- Evals API
- `/v1/fine_tuning/jobs` -- Fine-tuning (local equivalent: axolotl, trl, llama-factory, MLX-LM, unsloth, all of which consume the same JSONL format 'MiOS' ships)
- `/v1/files` with `purpose: "assistants"` -- file uploads for File Search

The KB ships local-compatible alternatives for each (see top-level
`README.md` § "Day-0 local-model compatibility").

### 4.4 Tooling for KB development

- OpenAI Cookbook: https://cookbook.openai.com/
- OpenAI Python SDK: https://github.com/openai/openai-python
- OpenAI Node SDK: https://github.com/openai/openai-node
- Tokenizer counts: `tiktoken` (`cl100k_base` for `text-embedding-3-*`, `o200k_base` for newer models): https://github.com/openai/tiktoken
- LangChain (alternative orchestration, OpenAI-compatible client): https://github.com/langchain-ai/langchain
- LlamaIndex (RAG framework): https://github.com/run-llama/llama_index
- DSPy (programmatic prompting): https://github.com/stanfordnlp/dspy
- Outlines (constrained generation, used by vLLM for strict mode): https://github.com/outlines-dev/outlines
- xgrammar (vLLM's other grammar engine): https://github.com/mlc-ai/xgrammar

### 4.5 Vector DBs for self-hosted RAG (consumes `chunks.jsonl`)

- pgvector: https://github.com/pgvector/pgvector
- Qdrant: https://qdrant.tech/
- Chroma: https://www.trychroma.com/
- Weaviate: https://weaviate.io/
- Milvus: https://milvus.io/
- LanceDB: https://lancedb.com/
- Faiss: https://github.com/facebookresearch/faiss

---

## 5. Citation Tier Reminder

| Tier | Definition | Examples in this KB |
|---|---|---|
| **Primary** | Official upstream documentation | `bootc.dev`, `osbuild.org`, `platform.openai.com`, `developers.openai.com`, `docs.redhat.com`, the 'MiOS' repo itself |
| **Secondary** | Official project repos (GitHub) | `bootc-dev/bootc`, `containers/composefs`, `ublue-os/ucore`, `sigstore/cosign`, `mios-dev/'MiOS'` |
| **Tertiary** | Vendor/community blogs corroborating primary sources | Fedora Magazine, Microsoft Learn, OpenAI Cookbook |
| **Validating** | Third-party format references for cross-checking OpenAI specs | DeepWiki, Leanware, Vectorize, CodeFriends |

When iterating on this KB, always cite **Primary** first; fall back to
Secondary; cite Tertiary/Validating only when Primary doesn't yet
document the surface (often the case for very recent OpenAI features).

---

## 6. Refresh Cadence

OpenAI surfaces change frequently. Recommended re-validation cadence:

- **Quarterly**: re-fetch all OpenAI docs URLs in §1; verify field names,
  required fields, and limits haven't shifted.
- **On every 'MiOS' minor release**: run the `mios_build_kb_refresh` tool to
  regenerate chunks from the live repo.
- **Immediately**: when OpenAI announces a new GA model, fine-tuning
  technique, or eval grader type -- extend the relevant section here and in
  the KB chunks.

---

<!-- v2-repo-grounded-addendum -->

## 7. v2 Repo-Grounded Findings (live-fetched 2026-05-02)

These corrections supersede the corresponding parts of v1. Each is
traceable to a specific 'MiOS' file fetched from
`github.com/mios-dev/'MiOS'@main`.

### 7.1 Repo structure as fetched

```
.devcontainer/  .github/  automation/  config/  etc/  tools/  usr/  v1/
.clinerules .cursorrules .editorconfig .gitattributes .gitignore
AGENTS.md usr/share/doc/mios/concepts/architecture.md CLAUDE.md CONTRIBUTING.md Containerfile
usr/share/doc/mios/guides/deploy.md usr/share/doc/mios/guides/engineering.md GEMINI.md Get-MiOS.ps1 usr/share/mios/ai/INDEX.md Justfile
LICENSE usr/share/doc/mios/reference/licenses.md README.md SECURITY.md usr/share/doc/mios/guides/self-build.md VERSION
build-mios.sh image-versions.yml install.ps1 install.sh
llms-full.txt llms.txt mios-build-local.ps1
preflight.ps1 push-to-github.ps1 renovate.json system-prompt.md
```

The repo root **is** the system root (no `system_files/` directory).

### 7.2 Architectural Laws -- verbatim from `usr/share/mios/ai/INDEX.md` §3

| # | Law | Enforced by |
| --- | --- | --- |
| 1 | **USR-OVER-ETC** -- static config in `/usr/lib/<component>.d/`; `/etc/` is admin-override only. Exceptions: `/etc/yum.repos.d/`, `/etc/nvidia-container-toolkit/`. | `automation/`, `usr/lib/`, `etc/` |
| 2 | **NO-MKDIR-IN-VAR** -- every `/var/` path declared via `usr/lib/tmpfiles.d/*.conf`. | `usr/lib/tmpfiles.d/mios*.conf` |
| 3 | **BOUND-IMAGES** -- every Quadlet image symlinked into `/usr/lib/bootc/bound-images.d/`. | `automation/01-system-files-overlay.sh:74-86` |
| 4 | **BOOTC-CONTAINER-LINT** -- final RUN of `Containerfile`. | `Containerfile` last `RUN` |
| 5 | **UNIFIED-AI-REDIRECTS** -- `MIOS_AI_KEY/MODEL/ENDPOINT`. No vendor URLs. | `usr/bin/mios`, `etc/mios/ai/` |
| 6 | **UNPRIVILEGED-QUADLETS** -- `User=`, `Group=`, `Delegate=yes` on every Quadlet. Documented exceptions: `mios-ceph`, `mios-k3s` as `User=root` (Ceph/K3s require uid 0). | `etc/containers/systemd/`, `usr/share/containers/systemd/` |

### 7.3 Service gating table -- verbatim from `usr/share/mios/ai/INDEX.md` §5

| Unit | Condition | Skips on |
| --- | --- | --- |
| `mios-ai` | `ConditionPathIsDirectory=/etc/mios/ai` | bootstrap incomplete |
| `mios-ceph` | `ConditionPathExists=/etc/ceph/ceph.conf`, `!container` | Ceph not configured, nested |
| `mios-k3s` | `!wsl`, `!container` | WSL2, nested containers |
| `crowdsec-dashboard` | `ConditionPathExists=/etc/crowdsec/config.yaml` | CrowdSec not configured |
| `mios-guacamole`, `guacd`, `guacamole-postgres` | `!container` | nested containers |
| `mios-pxe-hub` | `!wsl`, `!container` | virtualized hosts without routable LAN |
| `mios-gpu-{nvidia,amd,intel,status}` | `ConditionPathExists=/dev/...`, `!container`, `!wsl` (Intel) | no matching GPU device |
| `mios-llm-light` | none | always runs (llama.cpp behind the upstream llama-swap proxy; CPU-capable) |

### 7.4 Pipeline phases (verbatim from `usr/share/mios/ai/INDEX.md` §6 and `usr/share/doc/mios/guides/engineering.md`)

| Phase | Owner | Description |
| --- | --- | --- |
| Phase-0 | `mios-bootstrap.git/install.sh` | Preflight + profile load + identity capture |
| Phase-1 | `mios-bootstrap.git/install.sh` | Total Root Merge of `mios.git` and `mios-bootstrap.git` to `/` |
| Phase-2 | `Containerfile`/`automation/build.sh` | Build the running system (~48 numbered phase scripts) |
| Phase-3 | `mios.git/install.sh` + bootstrap profile staging | systemd-sysusers/tmpfiles/daemon-reload + user create + per-user `~/.config/mios/{profile.toml,system-prompt.md}` |
| Phase-4 | `mios-bootstrap.git/install.sh` | Reboot prompt |

### 7.5 Build-mode summary (verbatim from `usr/share/doc/mios/guides/self-build.md`)

| Mode | Path | Use |
| --- | --- | --- |
| 0 | `mios-bootstrap.git/install.sh` curl one-liner | initial install on fresh Linux |
| 1 | `.github/workflows/mios-ci.yml` | production CI (build → rechunk on tag → cosign keyless → push GHCR) |
| 2 | `mios-build-local.ps1` | Windows local 5-phase orchestrator |
| 3 | `Justfile` recipes | Linux local orchestrator |
| 4 | self-build (running 'MiOS' builds next 'MiOS') | `git clone && podman build && bootc switch --transport containers-storage localhost/mios:rechunked` |
| 5 | `config/ignition/` Butane configs → `.ign` | fully automated builds on fresh Fedora CoreOS / Fedora Server |

### 7.6 Justfile recipe inventory (verbatim from `Justfile`)

`preflight`, `flight-status`, `init`, `deploy`, `live-init`, `lint`,
`build`, `build-logged`, `build-verbose`, `embed-log`, `artifact`,
`cloud-build`, `rechunk`, `raw`, `iso`, `qcow2`, `vhdx`, `wsl2`,
`log-bootstrap`, `build-and-log`, `all-bootstrap`, `sbom`,
`init-user-space`, `reinit-user-space`, `show-user-space`,
`show-env`, `edit-env`, `edit-images`, `edit-build`, `edit-flatpaks`.

### 7.7 Containerfile structure (verbatim from `Containerfile`)

- Single-stage main build (`FROM ${BASE_IMAGE}`) plus a `ctx` scratch
  stage that COPYs `automation/`, `usr/`, `etc/`, `usr/share/mios/PACKAGES.md`,
  `VERSION`, `config/artifacts/`, `tools/` into `/ctx`.
- One large `RUN` block bind-mounts `/ctx` read-only and a writable
  `/tmp/build` copy, sources `automation/lib/packages.sh`, runs
  `dnf clean metadata`, `install_packages_strict base`, optionally
  writes `/usr/share/mios/flatpak-list` from `MIOS_DESKTOP_FLATPAKS`,
  runs `automation/01-system-files-overlay.sh` pre-pipeline, then
  `CTX=/tmp/build /tmp/build/automation/build.sh` to iterate
  `automation/[0-9][0-9]-*.sh`.
- Final two `RUN` instructions: `ostree container commit`, then
  `bootc container lint` (LAW 4, MUST be the final instruction).
- OCI labels: `containers.bootc=1`, `ostree.bootable=1`, plus
  `org.opencontainers.image.{title,description,licenses,source,version}`.
- `CMD ["/sbin/init"]`.

### 7.8 SECURITY.md kargs corrections (verbatim)

| Parameter | Active in 'MiOS'? | Rationale (per SECURITY.md) |
| --- | :-: | --- |
| `slab_nomerge` | [ok] | Heap isolation |
| `init_on_alloc=1` |  | Disabled -- CUDA memory init failures |
| `init_on_free=1` |  | Disabled -- same |
| `page_alloc.shuffle=1` |  | Disabled -- NVIDIA driver instability |
| `randomize_kstack_offset=on` | [ok] | Per-syscall stack randomization |
| `pti=on` | [ok] | Meltdown |
| `vsyscall=none` | [ok] | Legacy table off |
| `iommu=pt` | [ok] | VFIO passthrough |
| `amd_iommu=on` / `intel_iommu=on` | [ok] | IOMMU enable |
| `nvidia-drm.modeset=1` | [ok] | GNOME Wayland |
| `lockdown=integrity` | [ok] | (NOT confidentiality -- chosen for kexec compatibility) |
| `spectre_v2=on`, `spec_store_bypass_disable=on`, `l1tf=full,force`, `gather_data_sampling=force` | [ok] | Side-channel mitigations |

### 7.9 SELinux modules (verbatim from `SECURITY.md` §SELinux)

`mios_portabled`, `mios_kvmfr`, `mios_cdi`, `mios_quadlet`, `mios_sysext`
in `usr/share/selinux/packages/mios/`. Booleans: `container_use_cephfs`,
`virt_use_samba`. Fcontext: `/var/home(/.*)?` → `user_home_dir_t`.

### 7.10 Composefs config (verbatim from `usr/lib/ostree/prepare-root.conf`)

```ini
[composefs]
enabled = true

[etc]
transient = true

[root]
transient-ro = true
```

### 7.11 Bootstrap repo (separate, owns Phase-0/1/4)

`https://github.com/mios-dev/mios-bootstrap`

Owns the user-facing installer, identity capture, Total Root Merge, and
final reboot prompt. The `mios-dev/'MiOS'` repo (this KB's subject) is
the system layer.

---

## 8. Day-0 Local-Model Compatibility Matrix (additive)

The KB is portable across every OpenAI-API-compatible runtime. The
table below is the canonical compatibility surface -- keep it in sync
with `README.md` §"Day-0 local-model compatibility".

| Runtime | Endpoint | Chat | Embed | Tools | Strict | VStores | Resp | Batch | Evals | FT |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| OpenAI cloud | `https://api.openai.com/v1` | [ok] | [ok] | [ok] | [ok] | [ok] | [ok] | [ok] | [ok] | [ok] |
| Azure OpenAI | (your resource) | [ok] | [ok] | [ok] | [ok] | [ok] | [!] region | [ok] | [ok] | [ok] |
| **'MiOS' llm-light** (canonical, LAW 5) | `$MIOS_AI_ENDPOINT` | [ok] | [ok] | [ok] | [!] ignored |  |  |  |  |  |
| Ollama (retired as a MiOS lane) | upstream Ollama default `/v1` | [ok] | [ok] | [ok] | [!] ignored |  |  |  |  | external |
| vLLM | `http://localhost:8000/v1` | [ok] | [ok] | [ok] | [ok] via xgrammar |  | partial |  |  | external |
| LM Studio | `http://localhost:1234/v1` | [ok] | [ok] | [ok] | [!] ignored |  |  |  |  |  |
| llama.cpp server | `http://localhost:8080/v1` | [ok] | [ok] | [ok] via grammars | [!] ignored |  |  |  |  | external |
| LiteLLM proxy | `http://localhost:4000/v1` | [ok] | [ok] | [ok] proxied | [ok] proxied | proxied | translates | proxied | proxied | proxied |
| OpenRouter | `https://openrouter.ai/api/v1` | [ok] | partial | [ok] | per-model |  |  |  |  |  |

`[!] ignored` = the runtime accepts `strict: true` as an unknown field
and proceeds without enforcement (schema useful as documentation but
runtime does not reject malformed JSON).

### Local fine-tuning toolchains (consume the same `sft.jsonl`/`dpo.jsonl`)

- axolotl: <https://github.com/axolotl-ai-cloud/axolotl>
- TRL (HuggingFace): <https://github.com/huggingface/trl>
- llama-factory: <https://github.com/hiyouga/LLaMA-Factory>
- MLX-LM (Apple Silicon): <https://github.com/ml-explore/mlx-examples/tree/main/llms>
- unsloth: <https://github.com/unslothai/unsloth>

### Constrained-generation engines (enforce JSON Schema locally)

- xgrammar (vLLM default): <https://github.com/mlc-ai/xgrammar>
- Outlines: <https://github.com/dottxt-ai/outlines>
- llama.cpp grammars (GBNF): <https://github.com/ggerganov/llama.cpp/tree/master/grammars>

### Vector DBs (consume `chunks.jsonl`)

- pgvector -- Postgres extension: <https://github.com/pgvector/pgvector>
- Qdrant (used in `ingest_local.py`): <https://qdrant.tech/>
- Chroma: <https://www.trychroma.com/>
- Weaviate: <https://weaviate.io/>
- Milvus: <https://milvus.io/>
- LanceDB: <https://lancedb.com/>
- Faiss (in-process): <https://github.com/facebookresearch/faiss>

---

## 9. KB Refresh Pointers (additive -- what to ingest next)

When `mios_build_kb_refresh` re-runs, prioritize these still-unscraped
files that this v2 pass referenced but did not chunk to file-content
detail:

- `automation/build.sh` -- orchestrator entrypoint
- `automation/lib/{common,packages,masking}.sh` -- shared lib functions
- `usr/share/mios/PACKAGES.md` -- actual fenced-block contents
- `usr/share/mios/ai/system.md` -- canonical agent prompt
- `usr/share/mios/ai/v1/{models,mcp}.json` -- actual catalogs
- All `automation/[0-9][0-9]-*.sh` (~48 files)
- All `etc/containers/systemd/mios-*.container` Quadlets
- All `usr/lib/bootc/kargs.d/*.toml` (00-mios.toml seen in references; 05-mios-plymouth.toml inferred; others may exist)
- `usr/lib/sysctl.d/99-mios-hardening.conf` -- actual sysctl values
- `usr/lib/ostree/prepare-root.conf` -- composefs config
- `usr/lib/tmpfiles.d/mios*.conf` -- `/var` declarations (LAW 2)
- `usr/share/selinux/packages/mios/*.te` -- five custom SELinux modules
- `config/artifacts/{bib,iso,qcow2,vhdx,wsl2}.toml` -- BIB configs
- `etc/fapolicyd/fapolicyd.rules` -- actual fapolicyd policy
- `.github/workflows/mios-ci.yml` -- actual CI pipeline (build/rechunk/sign/push)
- `tools/{preflight,mios-overlay,mios-sysext-pack,flight-control,init-user-space,log-to-bootstrap}.sh`, `tools/lib/userenv.sh`
