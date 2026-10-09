<!-- AI-hint: Entry point for the shipped MiOS documentation -- what lives in each area, generated from the AI-hint header of every doc so the index cannot fall behind the tree. -->

# MiOS documentation

<!-- MIOS-GEN:boilerplate:what-mios-is -->
MiOS is one thing built two ways at once: an immutable, `bootc`/OCI-shaped
Fedora workstation -- the whole OS is a single container image, so `bootc
upgrade` behaves like a `git pull` and `bootc rollback` like a Ctrl-Z -- that
is *also* a local, self-hosted, agentic AI operating system.

<!-- derived from usr/share/mios/mios.toml [docs.boilerplate].what-mios-is -->
<!-- /MIOS-GEN:boilerplate:what-mios-is -->

This is the documentation baked into the image, readable on a booted host under
`/usr/share/doc/mios`. Every entry below is generated from that document's own
`AI-hint:` header, so this page cannot describe a file that no longer says what
it claims — see `reference/documentation-pipeline.md` for how that works.

## Start here

| If you want to | Read |
|---|---|
| understand what MiOS is and how it is put together | `concepts/architecture.md` |
| build, change or debug the image | `guides/engineering.md` |
| look up a port, a law, a build phase or a tool | `reference/` (below) |
| know why something was decided the way it was | `adr/` |
| read the prose distilled out of the source itself | `manual/` |

## reference

Lookup surfaces. Most of these are derived from `usr/share/mios/mios.toml`, so
they are correct by construction rather than by maintenance.

<!-- MIOS-GEN:index:usr/share/doc/mios/reference/*.md -->
| File | What it is |
|---|---|
| `usr/share/doc/mios/reference/ANTIFAB-FABRICATION-GAPS.md` | MiOS architectural documentation: Anti-Fabrication Gaps — Root-Cause + Fix-Design (LANE A). |
| `usr/share/doc/mios/reference/GLOBAL-UNIFICATION-PLAN.md` | The staged, lossless-diff-gated plan to collapse the duplicated/proliferated MIOS_* SSOT keys (measured 2523 keys, 79 version/image pairs, dead+alias dupes, one value copy-pasted across ~35 places)... |
| `usr/share/doc/mios/reference/PACKAGES.md` | Human-readable reference documentation for the MiOS RPM package ecosystem -- the irreducible host substrate beneath the bootc/OCI image. Agents should use mios.toml as the source of truth for package... |
| `usr/share/doc/mios/reference/PIPE-LEAK-RELIABILITY-GAPS.md` | MiOS architectural documentation: Pipe Leak & Reliability Gaps — root-cause + fix design. |
| `usr/share/doc/mios/reference/api.md` | Defines the OpenAI-compatible API surface that the unified MiOS AI endpoint (MIOS_AI_ENDPOINT, Architectural Law 5) targets, and maps it onto the MiOS inference lanes (mios-llm-light llama.cpp... |
| `usr/share/doc/mios/reference/audit-INDEX.md` | MiOS architectural documentation: Audit Index. |
| `usr/share/doc/mios/reference/audit-deploy-plane.md` | Audit of the MiOS DEPLOY plane (the least-done area, ~15-25%): traces the OFFLINE immutable-bootc install chain (Justfile oci-archive/BIB -> mios-stage-oci-archive -> tools/install.sh ->... |
| `usr/share/doc/mios/reference/audit-liquid-glass-shell.md` | Design for the MiOS liquid-glass desktop shell (Apple liquid-glass north star) projected FROM SSOT: adds a flat mios.toml [effects] section (blur/rounding/opacity/shadow/animation-curves) and... |
| `usr/share/doc/mios/reference/audit-mios-metal.md` | Concrete host definition for the MiOS-Metal split-plane: bootc hypervisor-router image contents, SSOT-driven vfio-pci bind, hand-authored `table inet mios-router` nft ruleset, headscale mesh join,... |
| `usr/share/doc/mios/reference/audit-numbering-unification.md` | Honest census + corrected unification design for MiOS's many build/system numbering schemes. Splits the SPARSE banded STAGE IDENTITY (automation/NN-name.sh prefix == [NN-name] log label, the... |
| `usr/share/doc/mios/reference/audit-publish-pipeline.md` | Robustness audit of the MiOS publish/bake pipeline (GitHub + Forgejo -> ghcr.io/mios-dev/mios:latest); catalogs every at-risk bare `podman build` (nested-caps exit-125 class) and every `x=$(cmd on... |
| `usr/share/doc/mios/reference/audit-runtime-wire.md` | Per-feature audit of MiOS's shipped-but-inert runtime features (greenboot, clevis/LUKS, chrony, ROCm/venus, ceph, mdevctl, freeipa/lldap, nut, guacamole/guacd, virt-v2v) classifying each as... |
| `usr/share/doc/mios/reference/audit-security.md` | Prioritized MiOS security-audit remediation plan (P0..P2) with file:line evidence and drop-in artifacts: PAT rotation + secret-store, cosign sign->VERIFY gate (CI + runtime policy.json from SSOT),... |
| `usr/share/doc/mios/reference/audit-tech-debt.md` | Measured refresh of the MiOS tech-debt map (ADR-0011 territory) -- server.py split-seam manifest, kill-eval status, shellcheck warning-ratchet upgrade, compiled-template + Law-14 language-policy... |
| `usr/share/doc/mios/reference/audit-value-dup-report.md` | MiOS architectural documentation: Value Duplication Audit Report. |
| `usr/share/doc/mios/reference/build-network-policy.md` | Technical reference on MiOS build-time network fetch policy, retry requirements, and degrade-open classification rules. |
| `usr/share/doc/mios/reference/build-pipeline.md` | The numbered build pipeline and the Law-6 root Quadlet exceptions, both derived from mios.toml so they cannot go stale. |
| `usr/share/doc/mios/reference/build-scripts.md` | Reference snapshot bundling the source and execution order of the scripts that build the MiOS OCI image, so an agent can locate and read specific build logic without crawling the repo. Curated... |
| `usr/share/doc/mios/reference/cli.md` | Derived reference documentation for the mios CLI verbs and helper backends, derived directly from mios.toml [verbs]. |
| `usr/share/doc/mios/reference/credits.md` | Attribution registry documenting all upstream projects, dependencies, and components used in MiOS to provide legal and source-of-truth tracking for the system's foundational substrate. |
| `usr/share/doc/mios/reference/deploy-plane-state.md` | The measured state of the MiOS deploy plane: what installs, what does not, and the root causes behind each. |
| `usr/share/doc/mios/reference/documentation-pipeline.md` | How MiOS documentation is produced -- AI-hints stay in source and are projected forward, comments are scraped, sanitized and distilled into the manual on a daily pass. |
| `usr/share/doc/mios/reference/drift-gates.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/reference/engineering-reference.md` | Comprehensive architectural reference for MiOS as a whole system -- an immutable bootc/OCI Fedora workstation that is also a local, self-replicating agentic AI OS. Maps the build pipeline, repository... |
| `usr/share/doc/mios/reference/events.md` | Derived reference documentation for observability event schema kinds. |
| `usr/share/doc/mios/reference/everything-db-driven.md` | WS-VECTOR research + workflow -- make EVERYTHING in MiOS DB-driven + vectorized: mios.toml is the cold image-baked authoring seed, Postgres/pgvector (mios-pgvector, db=mios, /var) is the LIVE runtime... |
| `usr/share/doc/mios/reference/features.md` | Derived reference documentation for blade capabilities and features. |
| `usr/share/doc/mios/reference/heavy-model-selection-2026-07.md` | Heavy-lane model selection for the shared 24GB RTX 4090 (2026-07). Decides MIOS_VLLM_BAKE_MODEL from a 14-candidate research pass. |
| `usr/share/doc/mios/reference/hwcaps.md` | Documentation for x86-64 microarchitecture optimization levels (v1-v4) used to determine which glibc-hwcaps packages to include in the build via the [hwcaps] table in mios.toml; explains the... |
| `usr/share/doc/mios/reference/install-ordering.md` | The WS-DEPLOY workstream -- refactor + reorder the MiOS install/first-boot pipeline into a logical dependency DAG so a "missing dependency / not-ready / not-yet-built" state is structurally... |
| `usr/share/doc/mios/reference/laws.md` | Architectural Laws and root exception table for MiOS, derived directly from mios.toml [laws] and [security.privileged_quadlets]. |
| `usr/share/doc/mios/reference/licenses.md` | Documents the legal licensing for MiOS components, including third-party drivers (NVIDIA), middleware (systemd, Mesa), and firmware, providing a reference for compliance and license attribution. |
| `usr/share/doc/mios/reference/lossless-diff-refactor.md` | Document describing the refactor methodology and invariant enforcement for the Global Unification Plan (GUP) and environment configuration in MiOS. |
| `usr/share/doc/mios/reference/maturity-and-release-runbook.md` | Standard operating procedure for MiOS R14 maturity review — verifying the sibling unit suites, producing the OCI image + disk artifacts via the Justfile, and the operator-gated signing (cosign/Secure... |
| `usr/share/doc/mios/reference/metal-vs-hosted.md` | GENERATED by mios-gen metal-vs-hosted from mios.toml. DO NOT EDIT -- re-run the generator. Part 1 compares the two PRODUCTS ([blade.planes]); Part 2 the two MODES ([blade.archetypes]). Every count is... |
| `usr/share/doc/mios/reference/naming-unification.md` | The WS-NAME workflow -- a TRUE GLOBAL minification of MiOS naming: collapse the entire TOML-key/env-var/verb/const surface onto ONE deterministic, capability-matched unified names+keys registry... |
| `usr/share/doc/mios/reference/nested-podman-caps.md` | Technical reference on nested podman-in-podman container capabilities and security flags. |
| `usr/share/doc/mios/reference/orchestration.md` | Technical reference for the MiOS agentic routing and orchestration decision seams. |
| `usr/share/doc/mios/reference/pipeline.md` | Derived reference documentation for the numbered MiOS build pipeline phases, derived directly from mios.toml [build.phases]. |
| `usr/share/doc/mios/reference/ports-and-laws.md` | Reference page for the port allocation table and the numbered Architectural Laws. The tables between MIOS-GEN markers are DERIVED from mios.toml by `mios-manual render`; the prose around them is... |
| `usr/share/doc/mios/reference/ports.md` | Machine-generated reference documentation for MiOS host and container port allocations, derived directly from mios.toml [ports] and [ports.categories]. |
| `usr/share/doc/mios/reference/shim-links.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/reference/sources.md` | Consolidates authoritative documentation links and technical specifications for OpenAI APIs, vector stores, and MiOS-specific knowledge base construction to ensure agent compliance with upstream... |
| `usr/share/doc/mios/reference/tool-index.md` | Index of every shipped MiOS tool, generated from the AI-hint header each one already carries. |
| `usr/share/doc/mios/reference/tree.md` | Annotated directory tree of the MiOS source and deployment root, providing a map of file functions, cross-references, and entry points for agents to navigate the filesystem and build pipeline. MiOS... |
| `usr/share/doc/mios/reference/units.md` | Derived reference documentation for systemd unit files shipped with MiOS. |
| `usr/share/doc/mios/reference/upstream-gaps-2026-07.md` | Prioritized upstream-vs-MiOS gap report (2026-07) across seven core subsystems. |
| `usr/share/doc/mios/reference/upstream-gaps-2026-08.md` | Verified upstream-vs-MiOS report (2026-08) for the AI-lane container images and their CVE exposure. Continues upstream-gaps-2026-07.md. |
| `usr/share/doc/mios/reference/upstream-registry.md` | Comprehensive Upstream Project & Technology Registry for MiOS. |

<!-- derived from the AI-hint headers of 49 file(s) matching usr/share/doc/mios/reference/*.md -->
<!-- /MIOS-GEN:index:usr/share/doc/mios/reference/*.md -->

## concepts

<!-- MIOS-GEN:index:usr/share/doc/mios/concepts/*.md -->
| File | What it is |
|---|---|
| `usr/share/doc/mios/concepts/OFFLINE-FIRST.md` | Defines MiOS's offline-first capability matrix — maps each lifecycle phase (overlay, pull, build, deploy, run, host, re-build, use-AI) to its network requirement, proving the whole bootc/OCI... |
| `usr/share/doc/mios/concepts/a2a-passport-conformance-2026-06-20.md` | Research + conformance record for MiOS's A2A protocol surface and Open Agent Passport — the 2026 native standards for inter-agent interop and verifiable agent identity — and how MiOS exposes both... |
| `usr/share/doc/mios/concepts/agent-pipe-openai-standards-master-plan.md` | Architectural roadmap for the agent-pipe system within MiOS — establishes the 2-stage classify→execute routing, OpenAI API conformance, and the unified tools/skills/recipes capability catalog as the... |
| `usr/share/doc/mios/concepts/aios-engineering-blueprint.md` | The MiOS AIOS engineering blueprint -- maps the 5-phase Agentic-OS reference taxonomy (architecture / memory / orchestration / security / benchmarking) onto MiOS's ACTUAL code (every component tagged... |
| `usr/share/doc/mios/concepts/aios-implementation-plan.md` | Roadmap for the Agentic-OS (AIOS) transition, mapping a research survey onto MiOS's existing `agent-pipe` orchestrator and pgvector agent plane, and defining the offline-first, FOSS-compliant path... |
| `usr/share/doc/mios/concepts/architecture.md` | Defines the MiOS system architecture end-to-end -- the bootc/OCI image structure and lifecycle, CDI-based GPU acceleration, zero-trust security posture, the FHS-compliant filesystem layout, and the... |
| `usr/share/doc/mios/concepts/chat-quality-and-visibility-standards.md` | MiOS AI Chat — Quality + Full-Visibility Gap Register |
| `usr/share/doc/mios/concepts/coderun-sandbox.md` | Defines the isolated Podman/Landlock container that lets MiOS agents dry-run / test generated code before it touches the immutable host. Explains the defense-in-depth boundary (Quadlet user unit +... |
| `usr/share/doc/mios/concepts/computer-use-federation.md` | Conceptual documentation of the Computer-Use Federation, defining the unified MCP/A2A architecture for Linux/Wayland desktop control via the `cu_*` verb catalog, `mios-computer-use` executor, and... |
| `usr/share/doc/mios/concepts/container-os-runtime.md` | System concepts documentation for the MiOS Container-OS Runtime Architecture. |
| `usr/share/doc/mios/concepts/deploy-model.md` | Defines the MiOS deployment model and execution modes -- the mutable Fedora server leg with FHS overlay via build-mios.sh, the MiOS-Sudo identity configuration, the immutable bootc-install mode, and... |
| `usr/share/doc/mios/concepts/dism-native-windows-iso-2026-07-04.md` | Verified, source-cited research on building a DISM-native custom Windows 11 ISO that carries MiOS (the Windows-side layer + a WSL2 podman machine). Two synthesized 5-angle research passes: (A)... |
| `usr/share/doc/mios/concepts/ebpf-semantic-enforcement.md` | Architectural fact-check reconciling Userspace AIOS vs Kernel-Space eBPF/LSM semantic security enforcement. |
| `usr/share/doc/mios/concepts/firstboot-large-models-plan.md` | Plan to provision large AI models at FULL FIRST BOOT instead of baking them into the build-time OCI image, keeping the image lean and avoiding the bound-images bake layer-commit ceiling (build exit... |
| `usr/share/doc/mios/concepts/foss-upstream-map.md` | System concepts documentation for the MiOS FOSS Upstream-Scout Report. |
| `usr/share/doc/mios/concepts/igpu-wave0-hardware-probes-2026-10.md` | Wave-0 Hardware Verification Probes (task_190) for Multi-Vendor GPU Compute (WS-IGPU). Records empirical findings from real-hardware probes across Windows host and WSL2 for iGPU compute, heavy-lane... |
| `usr/share/doc/mios/concepts/image-resolution.md` | System concepts documentation for the MiOS Image Registry and Name Resolution Architecture. |
| `usr/share/doc/mios/concepts/living-wallpaper-engine.md` | Research for a MiOS "living wallpaper" -- a GPU-accelerated animated mesh-gradient background derived from the SSOT [colors] palette, cross-surface (Windows + Linux GNOME/Wayland + optional... |
| `usr/share/doc/mios/concepts/llamacpp-engine-conversion.md` | Records MiOS's completed inference-engine conversion to llama.cpp (via the upstream llama-swap proxy image) to unlock fleet-wide KV-cache checkpoint/restore/fork for the AIOS Context Manager; Ollama... |
| `usr/share/doc/mios/concepts/mesh-topology-and-wireguard-backbone.md` | Blueprint and architectural specification for the MiOS v4 multi-site blade mesh, WireGuard/Headscale backbone, and translocation. |
| `usr/share/doc/mios/concepts/mios-api-architecture.md` | Architecture specification for MiOS-API unified AI and system management planes. |
| `usr/share/doc/mios/concepts/mios-app-browser-portal-dashboard-design-2026-07-03.md` | Ground-truth design spec for MiOS's user-facing surfaces (Portal |
| `usr/share/doc/mios/concepts/mios-metal-architecture.md` | System concepts documentation for the MiOS-Metal Split-Plane Hypervisor-Router Architecture. |
| `usr/share/doc/mios/concepts/multi-agent-buildout-plan.md` | Defines the parallel execution strategy for multi-agent development of MiOS itself, specifying which AIOS workstreams are agent-parallelizable versus operator-gated and outlining the... |
| `usr/share/doc/mios/concepts/multi-agent-orchestration-strategies.md` | Reality-checked landscape of multi-agent orchestration strategies (OpenAI Agents SDK / Swarm, MCP, LangGraph/CrewAI/ADK, structured deliberation, document-mutation coordination,... |
| `usr/share/doc/mios/concepts/naming-refactor-plan.md` | Specifies the 2026 naming-refactor roadmap for MiOS, defining canonical conventions for code constants, system service identifiers, model/agent tags, and SSOT keys to ensure cross-component... |
| `usr/share/doc/mios/concepts/oscontrol-envgrounding-gaps-2026-06-20.md` | Upstream-gap research (2026-06-20, multi-agent) + ordered fix plan for reliable AIOS Windows OS-control (UIA-targeted type/verify) and native per-turn env grounding. Records the root cause of the... |
| `usr/share/doc/mios/concepts/pod-architecture-2026-06-22.md` | Concept documentation explaining the MiOS 3-pod architecture, port-minimization strategy, host services constraint, and SSOT-driven generation lifecycle. |
| `usr/share/doc/mios/concepts/postgres-pgvector-unification.md` | Concept brief on why and how MiOS unified its agent-plane datastore onto PostgreSQL + pgvector (the FOSS "back to SQL" agent-memory stack), defining the standard schema, the shared mios-pg-query... |
| `usr/share/doc/mios/concepts/remediation-report-reconciliation-2026-08-24.md` | Reconciles an external "MiOS remediation roadmap" report against the shipped tree: its subjects are name collisions, its four phases already ship here; the audit found two real gaps -- the unwired... |
| `usr/share/doc/mios/concepts/roadmap-snapshot-decomposition-2026-06-22.md` | Research-backed decomposition of a historical MiOS roadmap snapshot into a minified executive summary and detailed current-architecture documentation. Reconciles the snapshot against current MiOS... |
| `usr/share/doc/mios/concepts/session-distillation-2026-08.md` | Master architectural distillation and full-spectrum systems synthesis for MiOS. |
| `usr/share/doc/mios/concepts/uia-ai-integration-2026-06-22.md` | Concept documentation detailing the research and architecture for integrating native Windows UI Automation (UIA) into the MiOS AI agent ecosystem. |
| `usr/share/doc/mios/concepts/unified-ai-pipeline-2026-06-16.md` | Reference for the UNIFIED MiOS AI pipeline — how every front-end is a thin client to the agent-pipe orchestrator (port key `agent_pipe`), the route-by-source anti-fabrication grounding strategy,... |
| `usr/share/doc/mios/concepts/upstream-gap-plan-2026-06.md` | Roadmap and research synthesis for MiOS infrastructure updates — hardware-verified overrides for WSL2 iGPU compute, gated heavy-lane (SGLang/vLLM) memory management, GUI/remote-display GPU recovery,... |
| `usr/share/doc/mios/concepts/ws-0-preflight-findings-2026-06-20.md` | WS-0-PREFLIGHT read-first correction pass for the MiOS master plan — re-derives every "verified" baseline number against a pinned HEAD (8658df1) and strikes claims stale against the current tree... |
| `usr/share/doc/mios/concepts/ws-a3-central-path-cutover-worklist.md` | WS-A3 follow-up worklist -- the precise, code-grounded the legacy datastore->pg cutover checklist for the CENTRAL chat path (agent-pipe server.py + the OWUI pipe), which is intentionally NOT edited... |
| `usr/share/doc/mios/concepts/ws-a3-surreal-to-pg-cutover.md` | WS-A3 completion record -- the legacy datastore->Postgres+pgvector cutover for the agent CLIs: the mios-pg-query extended-protocol (--exec-json) parameter-binding keystone, parameterized knowledge... |
| `usr/share/doc/mios/concepts/ws-decompose-stage2-plan-2026-06-20.md` | Stage-2 execution plan for the WS-A11/WS-3 server.py decomposition -- maps each chat_completions intent branch to its Kernel manager + Dispatcher handler, the behaviour-parity test plan, and the... |
| `usr/share/doc/mios/concepts/ws-grounding-2026-06-20.md` | Code-grounding verdicts for the MiOS master plan — per-workstream DONE/PARTIAL/MISSING assessment against the live tree at HEAD 8658df1, with exact file:line anchors. The headline split: Track-A... |
| `usr/share/doc/mios/concepts/ws-remaining-implementation-plan-2026-06-20.md` | Decision-ready implementation plan for the 6 remaining MiOS WS-* architectural subsystems (GOAP planner, zero-trust federation, server.py strangler-fig, RLS, pods→k3s, self-improve loop) — for each:... |
| `usr/share/doc/mios/concepts/ws-subsystems-activation-2026-06-20.md` | Operator activation playbook for the default-off WS-* subsystems shipped 2026-06-20 (RLS, A2A signed principal, peer reputation, egress firewall, mTLS PKI, self-improve loop) -- what each does, its... |
| `usr/share/doc/mios/concepts/ws7-uki-fapolicyd.md` | Documentation of the WS-7 security architecture defining the transition from permissive fapolicyd observation to enforced execution whitelisting and verity-rooted UKI builds via mios.toml... |

<!-- derived from the AI-hint headers of 43 file(s) matching usr/share/doc/mios/concepts/*.md -->
<!-- /MIOS-GEN:index:usr/share/doc/mios/concepts/*.md -->

## guides

<!-- MIOS-GEN:index:usr/share/doc/mios/guides/*.md -->
| File | What it is |
|---|---|
| `usr/share/doc/mios/guides/agent-windows-ssh.md` | Guide for the agent's Windows-host control bridge `/usr/libexec/mios/mios-windows`, which reaches the Windows host the WSL2 VM lives inside via TWO backends — WSL interop (default, no setup) and... |
| `usr/share/doc/mios/guides/blink-tmux-mobile-keys.md` | Guide to configuring Blink Shell on iOS (iPhone/iPad) and tmux for Shift+Tab and mobile terminal shortcut combos. |
| `usr/share/doc/mios/guides/blinkshell-codespaces-cloudshell.md` | Guide for connecting to GitHub Codespaces and Google Cloud Shell visual dev environments using Blink Shell on iOS. |
| `usr/share/doc/mios/guides/cephfs-xdg-storage.md` | Guides engineering reference documentation for the CephFS + XDG Unified Storage Fabric, documenting cache isolation rules, OCI bootstrap quickstarts, and multi-tenant extension paths. |
| `usr/share/doc/mios/guides/deploy.md` | Documentation for deploying a built MiOS image -- the OCI artifact plus its RAW/ISO/QCOW2/VHDX/WSL2 disk forms -- onto bootc-managed or FHS Fedora hosts, and the Day-2 bootc lifecycle... |
| `usr/share/doc/mios/guides/edge-node-join.md` | Operator guide for joining a Raspberry Pi / edge node to a MiOS council over the single outbound-dial port (agent-pipe, port key `agent_pipe`, MIOS_PORTS_AGENT_PIPE), using the three-layer mios.toml... |
| `usr/share/doc/mios/guides/engineering.md` | Defines the MiOS engineering standards — the 5-phase deployment pipeline, the `automation/` build sub-phase execution order, the `mios.toml` package-management schema, and the build-time conventions... |
| `usr/share/doc/mios/guides/hummingbird-distroless.md` | MiOS architectural documentation: Hummingbird: Distroless Agent-Pipe Service. |
| `usr/share/doc/mios/guides/inference-consolidation.md` | MiOS architectural documentation: Inference consolidation (WS-CONV-07). |
| `usr/share/doc/mios/guides/install.md` | Documentation for ingesting the 'MiOS' knowledge base into any OpenAI-API-compatible runtime; procedures for local inference (mios-llm-light, port key `llm_light`), pgvector RAG ingestion, and evals... |
| `usr/share/doc/mios/guides/memory-tiering.md` | MiOS architectural documentation: Memory Tiering Guide (WS-CONV-11). |
| `usr/share/doc/mios/guides/mobile-keybindings.md` | Shared SSOT shortcut contract for MiOS desktop, editor, native tmux and mobile SSH. |
| `usr/share/doc/mios/guides/security.md` | Documentation of MiOS security hardening posture, mapping kernel boot parameters, sysctl values, SELinux modules/booleans, firewalld ports, and supply-chain controls to the exact files that enforce... |
| `usr/share/doc/mios/guides/self-build.md` | Documentation for the MiOS self-build lifecycle, detailing the build chain, CI/CD workflows, and local build modes (Bootstrap, CI/CD, Windows, Linux/Justfile, in-place self-build, Ignition appliance)... |

<!-- derived from the AI-hint headers of 14 file(s) matching usr/share/doc/mios/guides/*.md -->
<!-- /MIOS-GEN:index:usr/share/doc/mios/guides/*.md -->

## upstream

What each upstream project is, and exactly how MiOS consumes it.

<!-- MIOS-GEN:index:usr/share/doc/mios/upstream/*.md -->
| File | What it is |
|---|---|
| `usr/share/doc/mios/upstream/bib.md` | Documentation for the bootc-image-builder (BIB) tool, detailing how MiOS uses it to transform the localhost/mios:latest OCI image into deployable disk artifacts (raw, anaconda-iso, qcow2, vhd→vhdx,... |
| `usr/share/doc/mios/upstream/bootc.md` | Documentation for the bootc tool, which MiOS uses as the primary mechanism for host-state mutations, image-based deployments, and system upgrades via OCI images. |
| `usr/share/doc/mios/upstream/cdi.md` | Documentation for the Container Device Interface (CDI) specification, detailing how MiOS abstracts NVIDIA, AMD ROCm/KFD, and Intel iGPU passthrough into a unified, vendor-agnostic layer that... |
| `usr/share/doc/mios/upstream/composefs.md` | Documentation for the composefs subsystem, detailing the integration of overlayfs, EROFS, and fs-verity to provide a verified, deduplicated, read-only root filesystem that anchors MiOS's image-mode... |
| `usr/share/doc/mios/upstream/cosign.md` | Documentation for Sigstore/Cosign keyless signing and verification — the specific CLI commands and OIDC identity that validate MiOS OCI container images and their attestations before bootc deploys... |
| `usr/share/doc/mios/upstream/crowdsec-fapolicyd-usbguard.md` | Documentation of the Layer 3 runtime security triplet (CrowdSec IPS, fapolicyd application whitelisting, USBGuard device whitelisting) — their configuration paths, operational modes, and roles in... |
| `usr/share/doc/mios/upstream/deploy-targets.md` | Documentation of MiOS deployment-target methods (bootc host, Hyper-V Gen2, QEMU/KVM, WSL2, Anaconda ISO, RAW disk), detailing the Justfile build recipes and per-artifact config requirements that turn... |
| `usr/share/doc/mios/upstream/dnf5.md` | Documentation for the dnf5 package manager as used at MiOS BUILD time, detailing critical build flags (`install_weak_deps`), BuildKit cache-mount patterns, kernel-package install restrictions, and... |
| `usr/share/doc/mios/upstream/fedora-bootc.md` | Documentation of the Fedora bootc upstream lineage, detailing how MiOS integrates Anaconda kickstarts, RHEL image-mode FIPS patterns, and why MiOS builds FROM ucore-hci (not fedora-bootc directly)... |
| `usr/share/doc/mios/upstream/ghcr.md` | Documentation for the GitHub Container Registry (GHCR) integration — the distribution endpoint for the single MiOS OCI image and the AI/quadlet images bound into it. Covers image paths, CI/user/bootc... |
| `usr/share/doc/mios/upstream/greenboot.md` | Documentation for greenboot, the automated post-boot health-check and auto-rollback system that protects MiOS's bootc image lifecycle — if required checks (composefs root integrity, mios-role,... |
| `usr/share/doc/mios/upstream/inference-engines.md` | Upstream reference for the three inference engines behind the MiOS lanes — llama.cpp via the llama-swap proxy (mios-llm-light, primary), vLLM (mios-llm-heavy, gated) and SGLang (mios-llm-heavy-alt,... |
| `usr/share/doc/mios/upstream/k3s-cockpit.md` | Documentation for the K3s + Cockpit (+ Ceph, libvirt/QEMU) cluster/admin surface of MiOS; explains how the same immutable bootc image that ships the desktop and the local agent stack can also grow... |
| `usr/share/doc/mios/upstream/looking-glass-kvmfr.md` | Documentation for the KVMFR kernel module and Looking Glass B7 client baked into the MiOS image — the shared-memory framebuffer relay that lets the host display a VFIO-passed-through guest GPU's... |
| `usr/share/doc/mios/upstream/nvidia.md` | Documentation of the NVIDIA GPU stack on MiOS, detailing the ucore-hci `:stable-nvidia` driver lineage, akmod build + MOK signing under kernel lockdown, NVIDIA-safe kargs, and CDI-based container GPU... |
| `usr/share/doc/mios/upstream/ostree.md` | Documentation of the ostree content-addressed filesystem used as the bootc storage backend to manage immutable system images, deployments, and the transition path to composefs. |
| `usr/share/doc/mios/upstream/pgvector.md` | Upstream reference for pgvector, the PostgreSQL vector extension behind the mios-pgvector unified agent datastore — what it is, how the ref floats on the pgNN family tag, how that flows through the... |
| `usr/share/doc/mios/upstream/podman.md` | Documentation for Podman, Buildah, Skopeo, and Quadlet integration in MiOS — defines the bootc image build invariants, multi-arch manifest rules, and the systemd-podman bridge that orchestrates every... |
| `usr/share/doc/mios/upstream/rechunk.md` | Documentation for the rechunk tool used during the MiOS build pipeline (Phase-3 / `just rechunk`) to optimize bootc upgrade deltas by consolidating OCI layers into a deterministic 67-layer structure,... |
| `usr/share/doc/mios/upstream/related-distros.md` | Provides a comparative analysis of MiOS against sibling Universal Blue images and other atomic/immutable distributions to define MiOS's specific positioning as a single immutable bootc/OCI Fedora... |
| `usr/share/doc/mios/upstream/secureblue.md` | Maps MiOS's defense-in-depth security posture onto the SecureBlue audit framework it draws from — which kernel kargs, sysctl values, and hardening policies MiOS adopts, which it deliberately diverges... |
| `usr/share/doc/mios/upstream/selinux.md` | Documentation of MiOS's SELinux posture — enforcing mode, the per-rule custom .te policy modules generated by automation/38-selinux.sh and the k3s policy from automation/37-k3s-selinux.sh, the... |
| `usr/share/doc/mios/upstream/ucore-hci.md` | Documents the ucore-hci upstream base image lineage and specifications — the FCOS/uCore/HCI/NVIDIA foundation MiOS builds FROM. Explains what the base image provides (immutable ostree+composefs,... |

<!-- derived from the AI-hint headers of 23 file(s) matching usr/share/doc/mios/upstream/*.md -->
<!-- /MIOS-GEN:index:usr/share/doc/mios/upstream/*.md -->

## adr

Architecture decisions. One decision per record, append-only: a superseding
decision is always a new record, never a rewrite of the old one.

<!-- MIOS-GEN:index:usr/share/doc/mios/adr/*.md -->
| File | What it is |
|---|---|
| `usr/share/doc/mios/adr/0001-two-gate-bake-activation.md` | Two orthogonal gates — BAKE (is it in the image?) vs ACTIVATION (does it start on THIS blade?) — give one universal image many roles with no image variants; read before touching the bound-image bake... |
| `usr/share/doc/mios/adr/0002-mios-sys-shared-base-consolidation.md` | Collapse the ~18-image sidecar fleet onto TWO shared-base images (mios-sys CUDA-free + mios-cuda) to cut the bound-image store ~60GB→~25GB; read before migrating any sidecar's Image=/Exec= or... |
| `usr/share/doc/mios/adr/0003-sbom-not-hardcode.md` | SSOT image/artifact refs carry TAG intent only; every sha256 digest / hash / checksum / resolved version is SBOM data resolved+recorded at BUILD, never hand-pinned — read before adding or "fixing"... |
| `usr/share/doc/mios/adr/0004-github-forgejo-equal-publisher.md` | GitHub Actions and the self-hosted Forgejo runner are EQUAL bit-for-bit build/publish environments; the CI PUBLISH env is a capacity gate (a standard runner can't hold the ~60GB bake), NOT a demotion... |
| `usr/share/doc/mios/adr/0005-sovereign-run-off-m-drive.md` | Deploy the universal MiOS image as a Hyper-V Gen 2 VM booting a .vhdx on M: cut by bootc install/BIB, because the installer factory-populates /var + /var/home that a raw podman-export skips — read... |
| `usr/share/doc/mios/adr/0006-openai-api-only-ai-contract.md` | The OpenAI public /v1 API surface is the ONE addressable AI contract across all of MiOS, exposed through a single front door (MIOS_AI_ENDPOINT, agent-pipe, port key `agent_pipe`); read before adding... |
| `usr/share/doc/mios/adr/0007-governance-model-laws-adrs-spec.md` | The governance model — laws are enforced invariants (fitness functions), ADRs are decisions, and a generated MiOS Spec renders both; read before "converting" laws to anything. |
| `usr/share/doc/mios/adr/0008-mios-cat-unified-entry-and-minification.md` | MiOS-Cat is the ONE unified front door (stage/install/build/update/provision/manual) that deploys the whole system on every platform off a SMALL always-present MiOS-Repo shadow-config USB partition... |
| `usr/share/doc/mios/adr/0009-unified-config-surface.md` | mios.toml (the SSOT), mios.html (the configurator), and the MiOS Portal are ONE config surface served on the `agent_pipe` port by agent-pipe — the same single front door that serves the OpenAI /v1... |
| `usr/share/doc/mios/adr/0010-ssot-as-system-dotfiles.md` | mios.toml IS the cross-platform system dotfiles-as-code. One SSOT + a [dotfiles.registry.<surface>] map + the generalized mios-theme-render/mios-dotfiles-render engine project EVERY declared dotfile... |
| `usr/share/doc/mios/adr/0011-unified-languages-and-file-patterns.md` | Unify the codebase to language-per-domain (Rust/Go for resilient native tooling — build driver, drift-runner, the verb dispatcher that removes the eval surface; Bun/TS for the Portal/configurator;... |
| `usr/share/doc/mios/adr/0012-float-latest-no-hand-pinned-versions.md` | Float-latest / no-hand-pinned-version principle: SSOT carries version intent (:latest/newest); build resolves and records exact provenance in SBOM. |
| `usr/share/doc/mios/adr/0013-deploy-surface-consolidation.md` | Single front door for AGY tree deployment: installation/mios-install resolves targets to underlying entrypoints without modifying existing scripts. |
| `usr/share/doc/mios/adr/0014-bootc-install-bare-metal-leg.md` | Architecture decision for installing MiOS onto hardware: the image's own bootc runs in podman via mios-install (to-disk, to-existing-root, to-filesystem). |
| `usr/share/doc/mios/adr/0015-unified-key-library-architecture.md` | Unified key library architecture defining single-source rule, derive rules, centralized COMPAT-ALIAS table, and enforcement gates. |
| `usr/share/doc/mios/adr/0016-blade-node-topology.md` | The Blade-Node topology decision: what a blade is, what a node is, how a MiOS addresses a service that lives on another machine, and why "MiOS-Metal" currently names three different things.... |
| `usr/share/doc/mios/adr/0017-blade-workload-mobility.md` | Workload mobility across the blade mesh: scheduling, fallbacks, failover, and divergence. |
| `usr/share/doc/mios/adr/0018-shutdown-diff-snapshotting-and-boot-cycle-accrual.md` | Captures pre-poweroff diffs, accrues them across boot cycles, provides interactive HITL review, and rolls approved diffs into new immutable OCI layers. |
| `usr/share/doc/mios/adr/0019-preemptive-scheduling-and-priority-gated-deliberation.md` | Defines engine-level priority preemption and consequentiality-gated Deliberative Collective Intelligence (DCI). |
| `usr/share/doc/mios/adr/0020-edge-mesh-binary-wire-protocol-and-dual-tier-sandboxing.md` | Specifies the 16-byte binary wire protocol, dual-tier Wasm/container sandboxing, and hierarchical work-stealing for mios-node. |
| `usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md` | Consolidate the ~1,500-file script estate into a handful of function-named Rust static binaries (gate/gen/resolve/serve/probe) over a shared mios-ssot crate, ported strangler-style one gate at a time... |
| `usr/share/doc/mios/adr/0022-version-floating-and-sidecars.md` | ADR 0022: Version Floating, Image Sidecars, and Offline Vendoring Policy. |
| `usr/share/doc/mios/adr/0023-unified-native-resolver.md` | ADR 0023: Unified Native Resolver Architecture. |
| `usr/share/doc/mios/adr/0024-vscode-client-scoped-settings.md` | ADR-0024 partitions the VS Code SSOT by mios.toml [dotfiles.vscode]: devcontainer.json / *.code-workspace blocks get only client-portable keys, settings FILES the full profile; sync-dotfiles.py... |
| `usr/share/doc/mios/adr/0025-core-image-profile.md` | ADR-0025 declares the core MiOS profile once in mios.toml [profiles]; every image kind (OCI, WSL2, devcontainer, cloud projection, Codespace) is the root pipeline run under a profile, and the... |
| `usr/share/doc/mios/adr/0026-global-task-store.md` | ADR-0026 makes one strict-schema JSONL file the canonical record of every MiOS task (T-, AGY-, MON-, F-, M-, CODE-, G- ids), merged losslessly from every task list with verbatim provenance; the pure... |
| `usr/share/doc/mios/adr/0027-dual-tier-oci-ai-artifacts.md` | Dual-tier OCI AI artifact architecture: standard OCI ModelCars for resident inference lanes via Podman Quadlet image mounts and composefs; CNCF KitOps ModelKits for development, training corpora,... |
| `usr/share/doc/mios/adr/0028-one-canonical-task-list.md` | ADR-0028 makes tasks.jsonl at the repo root the only canonical MiOS task list, edited directly; TASKS.md is its rendered documentation plus an operator-overrides block every task tool applies; the... |
| `usr/share/doc/mios/adr/README.md` | Index + process spec for MiOS Architecture Decision Records; read this first to learn the ADR format, status lifecycle, and which ADR governs the workstream you are implementing. |

<!-- derived from the AI-hint headers of 29 file(s) matching usr/share/doc/mios/adr/*.md -->
<!-- /MIOS-GEN:index:usr/share/doc/mios/adr/*.md -->

## manual

Per-area prose, distilled from the source comments by the daily pass. Each
passage carries an anchor back to the comment it came from.

<!-- MIOS-GEN:index:usr/share/doc/mios/manual/*.md -->
| File | What it is |
|---|---|
| `usr/share/doc/mios/manual/.devcontainer.md` | Manual pages distilled from the source comments of .devcontainer, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/_harvest/.devcontainer.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/agent-pipe.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/ai.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/audio.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/automation.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/db.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/deploy.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/hermes.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/kargs.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/lib.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/llamacpp.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/localsearch-3.service.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/localsearch-control-3.service.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/localsearch-writeback-3.service.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/log.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/mcp.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/mem.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/mios.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/net.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/node.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/profile.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/sec.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/src.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/storage.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/sysctl.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/system.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/systemd.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/sysusers.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/tests.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/tmpfiles.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/tools.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/user.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/ux.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/virt.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/wanted.d.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/_harvest/win.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/a2a.md` | Manual pages distilled from the source comments of a2a, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/access.md` | Manual pages distilled from the source comments of access, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/agent-pipe.md` | Manual pages distilled from the source comments of agent-pipe, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/agent_pipe.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/ai.md` | Manual pages distilled from the source comments of ai, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/audio.md` | Manual pages distilled from the source comments of audio, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/automation.md` | Manual pages distilled from the source comments of automation, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/boot.md` | Manual pages distilled from the source comments of boot, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/btop.md` | Manual pages distilled from the source comments of btop, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/ch01-introduction-and-core-concepts.md` | Chapter 01: Introduction and Core Concepts. Defines the dual nature of MiOS as an immutable, bootc Fedora workstation and a local agentic OS. Explains how the Git repository tree directly mirrors the... |
| `usr/share/doc/mios/manual/ch02-installation-and-deployment.md` | Chapter 02: Installation and Deployment. Covers provisioning the MiOS-DEV seed environment via Windows PowerShell or the Linux just runner. Outlines the provisioning sequence for the build plane,... |
| `usr/share/doc/mios/manual/ch03-system-configuration-and-governance.md` | Chapter 03: System Configuration and Governance. Explains the management of packages, AI lanes, and quadlets centrally via mios.toml. Maps configuration resolution precedence across vendor, host, and... |
| `usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md` | Chapter 04: The Agentic AI Stack. Describes the routing of all AI interactions through the MIOS_AI_ENDPOINT (Hermes gateway, port key hermes). Details the primary front door on the agent_pipe port... |
| `usr/share/doc/mios/manual/ch05-federation-and-computer-use.md` | Chapter 05: Federation and Computer Use. Details the standardized MCP interface utilized by agents to discover external tools. Documents the A2A JSON-RPC specifications for peer delegation. Explains... |
| `usr/share/doc/mios/manual/ch06-security-and-hardware-virtualization.md` | Chapter 06: Security and Hardware Virtualization. Explains composefs sealing of the read-only /usr directory and fs-verity. Details defense-in-depth mechanisms via CrowdSec, fapolicyd, and USBGuard.... |
| `usr/share/doc/mios/manual/ch07-cluster-and-storage-fabric.md` | Chapter 07: Cluster and Storage Fabric. Outlines the mechanisms for expanding the workstation into a Kubernetes cluster. Explains CephFS containerized storage deployments and privileged exemptions. |
| `usr/share/doc/mios/manual/ch08-bootloader-and-unified-kernel-images-uki.md` | Chapter 08: Bootloader and Unified Kernel Images (UKI). Covers compilation and structure of Unified Kernel Images via systemd-ukify. Details kernel module signing, trust models, and cryptographic... |
| `usr/share/doc/mios/manual/ch09-systemd-and-quadlet-orchestration.md` | Chapter 09: Systemd and Quadlet Orchestration. Defines user-space daemon layers and systemd-generator permissions configuration. Explains how podman quadlets render systemd unit files on startup.... |
| `usr/share/doc/mios/manual/ch10-local-inference-lanes-and-llama-cpp.md` | Chapter 10: Local Inference Lanes and llama.cpp. Covers how llama-swap handles hot swapping and KV paging on the `llm_light` port. Maps GPU context management, prompt template bindings, and model... |
| `usr/share/doc/mios/manual/ch11-heavy-gpu-lanes-and-sglang-vllm.md` | Chapter 11: The Heavy GPU Lane and its Engines (vLLM or SGLang). One heavy dGPU lane, mios-llm-heavy, whose SSOT spec carries the engine choice ([ai].heavy_engine) and renders the matching Quadlet.... |
| `usr/share/doc/mios/manual/ch12-unified-memory-and-pgvector-schema.md` | Chapter 12: Unified Memory and pgvector Schema. Details pgvector database container setup, connection pools, and permissions. Explains cosine-similarity searches utilizing vector retrieval. Covers... |
| `usr/share/doc/mios/manual/ch13-model-context-protocol-integration.md` | Chapter 13: Model Context Protocol Integration. Describes how to write custom Python or Go MCP servers. Covers how the AI gateway queries the system tool registry. Details how tools run in sandboxed... |
| `usr/share/doc/mios/manual/ch14-agent-to-agent-delegation-protocols.md` | Chapter 14: Agent-to-Agent Delegation Protocols. Details the communications standard and payload schema for agent delegation. Explains how the coding subagent (MiOS-OpenCode) takes over code... |
| `usr/share/doc/mios/manual/ch15-computer-use-and-desktop-control.md` | Chapter 15: Computer Use and Desktop Control. Details coordinate grounding on Wayland screens via vision models. Explains input emulation via the mios-pc-control command suite. Documents screen tree... |
| `usr/share/doc/mios/manual/ch16-immutable-root-and-composefs-sealing.md` | Chapter 16: Immutable Root and Composefs Sealing. Explains composefs structures and /usr partition read-only mounts. Covers system file validation against trusted cryptographic hashes. Describes how... |
| `usr/share/doc/mios/manual/ch17-defense-in-depth-hardening.md` | Chapter 17: Defense in Depth Hardening. Covers telemetry monitoring, IP bans, and custom local parsers. Details binary execution blocking on unauthorized directories. Explains protection policies... |
| `usr/share/doc/mios/manual/ch18-supply-chain-and-image-integrity.md` | Chapter 18: Supply Chain and Image Integrity. Defines policy-based verification of OCI signatures at pull time. Covers keyless image signing using OIDC identity providers. Explains the generation and... |
| `usr/share/doc/mios/manual/ch19-hardware-passthrough-and-vfio-pci.md` | Chapter 19: Hardware Passthrough and VFIO-PCI. Details binding GPUs to vfio-pci on boot, bypassing host drivers. Explains the XML schema mapping for physical GPU passthrough to guests. Documents... |
| `usr/share/doc/mios/manual/ch20-container-device-interface-plumbing.md` | Chapter 20: Container Device Interface Plumbing. Covers CDI spec generation for CUDA applications running in rootless podman. Explains ROCm/KFD driver mounts and container bindings. Documents Intel... |
| `usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md` | Chapter 21: Looking Glass B7 and KVMFR. Explains building and signing KVMFR module from source. Details allocations under /dev/shm for low-latency memory copy. Documents Wayland client build and... |
| `usr/share/doc/mios/manual/ch22-cpu-topology-and-performance-pinning.md` | Chapter 22: CPU Topology and Performance Pinning. Maps CPU pinning allocations for isolated workloads. Details memory node alignment for reduced guest latencies. Covers scheduling priority and... |
| `usr/share/doc/mios/manual/ch23-single-node-kubernetes-expansion.md` | Chapter 23: Single-Node Kubernetes Expansion. Covers resource boundaries between GNOME and K3s services. Details ingress routing rules in single-node clusters. Explains custom security policies... |
| `usr/share/doc/mios/manual/ch24-cephfs-local-storage-cluster.md` | Chapter 24: CephFS Local Storage Cluster. Covers Ceph Quadlet definitions and storage config. Details block device access exemptions. Maps user directories onto CephFS mounts for auto-backups. |
| `usr/share/doc/mios/manual/ch25-local-search-engine-and-searxng.md` | Chapter 25: Local Search Engine and SearXNG. Explains local container setup and engines configuration. Covers query routing from search tools to SearXNG. Details parsing HTML results into Markdown... |
| `usr/share/doc/mios/manual/ch26-unified-knowledge-base-ingestion.md` | Chapter 26: Unified Knowledge Base Ingestion. Explains document indexing and embedding tasks. Maps ingestion pipeline and database tables layout. Covers re-indexing databases and recall optimizations. |
| `usr/share/doc/mios/manual/ch27-shell-configuration-and-environment-cascade.md` | Chapter 27: Shell Configuration and Environment Cascade. Maps configuration overrides bubbling up to login shells. Covers theme configuration and prompt status icons. Documents timezone and UTF-8... |
| `usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md` | Chapter 28: Dynamic Network and Firewall Management. Covers managing port firewalls via firewalld command hooks. Explains how ports are dynamically resolved and bound. Documents Tailscale integration... |
| `usr/share/doc/mios/manual/ch29-web-management-and-configurator-ui.md` | Chapter 29: Web Management and Configurator UI. Covers configuration editing via the static index HTML form. Details how the UI panel maps active container metrics. Explains TOML serialization and... |
| `usr/share/doc/mios/manual/ch30-system-auditing-and-drift-verification.md` | Chapter 30: System Auditing and Drift Verification. Documents checks run by 99-postcheck.sh at build-time. Explains build constraints blocking hardcoded URLs or ports. Maps validation against our... |
| `usr/share/doc/mios/manual/ch31-desktop-applications-and-flatpaks.md` | Chapter 31: Desktop Applications and Flatpaks. Covers pre-downloading and staging Flatpaks inside the image. Explains locking Flatpak permissions using Flatseal overrides. Details sync hooks... |
| `usr/share/doc/mios/manual/ch32-swarm-worker-clusters.md` | Chapter 32: Swarm Worker Clusters. Covers dynamic worker provisioning via Quadlet templates. Details task partitioning and worker aggregation pipelines. Explains scheduling and routing algorithms... |
| `usr/share/doc/mios/manual/ch33-sandboxed-execution-and-coder-sandbox.md` | Chapter 33: Sandboxed Execution and Coder Sandbox. Covers configuring unprivileged containers for code interpretation. Details how policies restrict container sandbox processes. Explains output... |
| `usr/share/doc/mios/manual/ch34-identity-management-and-freeipa.md` | Chapter 34: Identity Management and FreeIPA. Covers configuring FreeIPA libraries inside Fedora overlay. Details staging user and system accounts prior to install. Explains automatic domain... |
| `usr/share/doc/mios/manual/ch35-system-monitoring-and-telemetry.md` | Chapter 35: System Monitoring and Telemetry. Covers collecting CPU, RAM, and GPU stats via node-exporters. Details tracking query duration, tokens, and routing lanes. Maps visual dashboards for... |
| `usr/share/doc/mios/manual/ch36-greenboot-health-check-and-recovery.md` | Chapter 36: Greenboot Health Check and Recovery. Covers greenboot scripts verifying service states. Explains atomic image swap checks triggered on boot failures. Documents dynamic cleanup tasks... |
| `usr/share/doc/mios/manual/ch37-gpu-capability-detection-and-passthrough-shims.md` | Chapter 37: GPU Capability Detection and Passthrough Shims. Covers spec updates triggered when hardware states change. Details device locking and lockouts during state transitions. Explains dynamic... |
| `usr/share/doc/mios/manual/ch38-remote-desktop-and-gnome-grd.md` | Chapter 38: Remote Desktop and GNOME GRD. Covers running GNOME inside headless Wayland sessions. Details TLS encryption and user credential checks. Documents setting up virtual display outputs on... |
| `usr/share/doc/mios/manual/ch39-host-guest-shared-filesystems.md` | Chapter 39: Host-Guest Shared Filesystems. Covers high-speed file sharing cache configurations. Details exposing system paths inside guest virtual overlays. Explains UID/GID mappings translation... |
| `usr/share/doc/mios/manual/ch40-system-log-aggregation.md` | Chapter 40: System Log Aggregation. Covers sync hooks pulling logs into bootstrap sectors. Details systemd service parameters for log copy tasks. Explains compiling system diagnostics into single... |
| `usr/share/doc/mios/manual/ch41-machine-owner-key-management.md` | Chapter 41: Machine Owner Key Management. Covers generating secure build-keys inside automation. Details UEFI enrollment prompts triggered on boots. Explains dynamic module signatures added on kernel... |
| `usr/share/doc/mios/manual/ch42-kernel-upgrade-and-build-pipelines.md` | Chapter 42: Kernel Upgrade and Build Pipelines. Covers base image upgrades and validation procedures. Details compilation gating rules verifying module states. Explains bootc-image-builder actions... |
| `usr/share/doc/mios/manual/ch43-local-registry-and-oci-distribution.md` | Chapter 43: Local Registry and OCI Distribution. Covers OCI distribution containers used in replication loop. Details cache boundaries speeding up successive image builds. Explains pulling local... |
| `usr/share/doc/mios/manual/ch44-host-package-overrides-and-dnf5.md` | Chapter 44: Host Package Overrides and DNF5. Covers configurations prioritization mappings. Details manual package installations resolving hardware conflicts. Explains troubleshooting procedures for... |
| `usr/share/doc/mios/manual/ch45-diagnostic-tools-and-profilers.md` | Chapter 45: Diagnostic Tools and Profilers. Covers physical adapter checks run by system-profilers. Details checks verifying container loopback containment. Explains comparing active setups against... |
| `usr/share/doc/mios/manual/ch46-user-persona-staging.md` | Chapter 46: User Persona Staging. Covers default accounts, credentials, and settings groups. Details template overlay merging home profile files. Explains isolation policies across different accounts. |
| `usr/share/doc/mios/manual/ch47-virtual-machine-templates.md` | Chapter 47: Virtual Machine Templates. Details template variables enabling vTPM and Secure Boot. Covers automating guest staging using init data. Explains hypervisor guest actions executed via virsh. |
| `usr/share/doc/mios/manual/ch48-local-ai-web-consoles.md` | Chapter 48: Local AI Web Consoles. Covers Open WebUI Quadlet parameters and local mapping. Details interface layout settings and custom models aliases. Explains console access security using token... |
| `usr/share/doc/mios/manual/ch49-offline-first-governance.md` | Chapter 49: Offline-First Governance. Covers staging local mirror caches inside container build overlay. Details models weights verification loaded under /srv/ai. Explains fallback behaviors... |
| `usr/share/doc/mios/manual/ch50-upstream-tracking-and-maintenance.md` | Chapter 50: Upstream Tracking and Maintenance. Covers checking changes between host and remote overlays. Details Justfile build automation and check goals. Explains checklist targets required to tag... |
| `usr/share/doc/mios/manual/ch51-distilled-system-knowledge-code-invariants.md` | Chapter 51: Distilled System Knowledge & Code Invariants. Losslessly distilled architectural knowledge, operational invariants and technical comments recovered from historical commits and component... |
| `usr/share/doc/mios/manual/ch52-multi-judge-consensus.md` | Chapter 52: Multi-Judge Consensus. Explains why one judge lane's yes/no is not enough to gate a pipeline, and how the weighted quorum replaces it. Covers the vote fold, abstention versus rejection,... |
| `usr/share/doc/mios/manual/ch53-drift-monitoring.md` | Chapter 53: Drift Monitoring. Explains the Jensen-Shannon Goodhart alarm that watches the agent plane's own verdict and intent distributions for a silent shift. Covers the bounded 0..1 divergence... |
| `usr/share/doc/mios/manual/ch54-agent-pipe-importability.md` | Chapter 54: Agent-Pipe Importability. Records the defect class that let agent-pipe's server module reference names nothing defines, the three undefined module-scope names that made it unimportable,... |
| `usr/share/doc/mios/manual/ch55-dead-schema-and-half-wired-units.md` | Chapter 55: Dead Schema and Half-Wired Units. Two fitness functions for things that exist but do nothing. check_schema_consumers requires every table in schema-init.sql to have a real code consumer,... |
| `usr/share/doc/mios/manual/ch56-persistent-shell-sessions.md` | Chapter 56: Persistent Shell Sessions. Explains the SHELL-01 substrate that lets cwd, environment and history survive across agent turns. Covers the BEGIN/END nonce framing and the two spoofing... |
| `usr/share/doc/mios/manual/ch57-powershell-object-flattening.md` | Chapter 57: PowerShell Object Flattening. Records why an object-returning cmdlet reached the model as a BLANK LINE rather than as noise, how a console-less runspace collapses every formatter column... |
| `usr/share/doc/mios/manual/ch58-roadmap-status-parity.md` | Chapter 58: Roadmap Status Parity. Records the drift that let TASKS.md answer "what is left?" two different ways -- a summary-table cell and the task's own Status line -- and the 49 rows where they... |
| `usr/share/doc/mios/manual/ch59-request-coalescing.md` | Chapter 59: Request Coalescing. Explains why MiOS deliberately does NOT client-side batch its own inference lanes -- vLLM, SGLang and llama.cpp already run continuous batching, so a second layer only... |
| `usr/share/doc/mios/manual/ch60-durable-quota.md` | Chapter 60: Durable Quota. Records why a per-principal budget that lives only in memory is not a budget at all -- every restart, including a bootc upgrade, hands an exhausted account a fresh... |
| `usr/share/doc/mios/manual/ch61-run-template-replay.md` | Chapter 61: Run-Template Replay. Records why the capture half of the run-template feature was write-only for so long: templates were keyed by a hash of the PLAN's shape, which can only be computed... |
| `usr/share/doc/mios/manual/ch62-sandbox-seccomp.md` | Chapter 62: Sandbox Seccomp. Records what the risk-tier dispatch sandbox actually did before task_210 -- bwrap really was exec'd and really did jail the filesystem and the network, while the confined... |
| `usr/share/doc/mios/manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md` | Chapter 63: Diff Snapshotting, Boot-Cycle Accrual & HITL Image Roll-in Pipeline. |
| `usr/share/doc/mios/manual/ch64-engine-preemptive-scheduling-and-dci-deliberation.md` | Chapter 64: AIOS Engine Preemptive Scheduling, Priority Gating & DCI Structured Deliberation. |
| `usr/share/doc/mios/manual/ch65-edge-micro-mesh-binary-wire-protocol-and-sandboxing.md` | Chapter 65: Edge Micro-Mesh 16-Byte Wire Protocol, Dual-Tier Sandboxing & Hierarchical Work-Stealing. |
| `usr/share/doc/mios/manual/ch66-v5-authority-inversion-and-cephfs-tiering.md` | Chapter 66: V5 Database Authority Inversion, CephFS Storage Tiering & Disaster Recovery. |
| `usr/share/doc/mios/manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md` | Chapter 67: Discrete GPU VFIO Passthrough, Looking Glass B6 IVSHMEM & Inter-VM Audio. |
| `usr/share/doc/mios/manual/ch68-living-wallpaper-shaders-and-ssot-theme-engine.md` | Chapter 68: Living Wallpaper WebGL Shaders, FOSS Licensing & Real-Time Theme Synchronization. |
| `usr/share/doc/mios/manual/ch69-autonomous-epistemic-evolution-and-learn-loop.md` | Chapter 69: Autonomous Epistemic Evolution, /learn Distillation & Model Fine-Tuning. |
| `usr/share/doc/mios/manual/ch70-upstream-technology-registry-and-ecosystem-advances.md` | Chapter 70: Upstream Ecosystem Registry, Upstream Adoption Playbook & FOSS Drift Auditing. |
| `usr/share/doc/mios/manual/ch71-composefs-sealing.md` | Chapter 71: Composefs fs-verity Root Filesystem Sealing and Atomic Validation (task_471, task_2704). Details kernel-level fs-verity digest verification, prepare-root composefs mount execution,... |
| `usr/share/doc/mios/manual/ch72-ide-custom-css.md` | Chapter 72: IDE Custom CSS and Edge-to-Edge Integrated Terminal. Covers provisioning the vscode-custom-css extension, the edge-to-edge terminal stylesheet, and the mios-vscode-custom-css orchestrator... |
| `usr/share/doc/mios/manual/ch73-cephfs-user-subvolumes.md` | Chapter 73: Global Per-User Encrypted CephFS Subvolumes. Covers the mios-user-cephfs subvolume manager that provisions isolated, fscrypt-encrypted CephFS subvolumes for each user across bare-metal... |
| `usr/share/doc/mios/manual/ch74-blade-pre-enrollment.md` | Chapter 74: Declarative SSOT Blade Pre-Enrollment Registry and TPM EK Fingerprint Parser (task_473, task_2706). Details TPM 2.0 Endorsement Key validation, SSOT schema for [cluster.blades],... |
| `usr/share/doc/mios/manual/ch75-greenboot-hardware-degrade.md` | Chapter 75: Peripheral Hardware Health Evaluator and Non-Fatal Greenboot Degradation Reporter (task_475, task_2708). Covers the degrade-not-refuse architectural philosophy, peripheral subsystem... |
| `usr/share/doc/mios/manual/ch76-remote-attestation.md` | Chapter 76: Automated RFC 9334 RATS Remote TPM 2.0 Quote Verifier and Zero-Touch Cluster Onboarding (task_474, task_2707). Details cryptographic challenge-response nonces, composite PCR digest... |
| `usr/share/doc/mios/manual/ch77-hardware-fallback.md` | Chapter 77: Automated Network and Audio Fallback Manager with Operator Desktop Alert Daemon (task_476, task_2709). Covers peripheral degradation recovery, secondary NIC and USB tethering... |
| `usr/share/doc/mios/manual/ch78-streaming-audio-whisper.md` | Chapter 78: Low-Latency WebRTC Streaming Audio Ingress and Streaming Whisper Speech-to-Text Bridge (task_477, task_2710). Details WebRTC ingress protocol, 16kHz PCM framing, sub-150ms VAD ring... |
| `usr/share/doc/mios/manual/ch79-streaming-tts-piper.md` | Chapter 79: Concurrent Streaming Piper TTS Audio Synthesis and PipeWire Buffer Feeder (task_478, task_2711). Details streaming TTS architecture, sentence segmentation, ONNX acceleration, PipeWire... |
| `usr/share/doc/mios/manual/ch80-hardware-video-encoding.md` | Chapter 80: Multi-Vendor Hardware Video Encoder Discovery and DMA-BUF Capture Bridge (task_479, task_2712). Details hardware ASIC probing (QuickSync, NVENC, AMF), AV1 > HEVC > AVC codec priority... |
| `usr/share/doc/mios/manual/ch81-video-streaming-benchmarks.md` | Chapter 81: Adaptive Bitrate and Low-Latency Frame Encoding Streaming Benchmark Suite (task_480, task_2713). Details remote desktop video streaming benchmarks, sub-12ms frame encoding SLA for 60 FPS,... |
| `usr/share/doc/mios/manual/ch82-mesh-workload-partitioning.md` | Chapter 82: Multi-Node Dynamic AI Workload Partitioning and Capability-Aware Task Routing (task_481, task_2714). Details 2-6 bare-metal Blade mesh topology discovery, capability matrix scoring,... |
| `usr/share/doc/mios/manual/ch83-task-failover-resilience.md` | Chapter 83: Automated Node Failure Detection and Zero-Loss Dynamic Task Re-Distribution Engine (task_482, task_2715). Details mesh heartbeat auditing, in-flight task leasing protocols, zero-loss... |
| `usr/share/doc/mios/manual/ch84-vision-redaction.md` | Chapter 84: ATSPI Accessibility Tree Sensitive Widget Coordinate Detector and Wayland Frame Blur Filter (task_483, task_2716). Details ATSPI role discovery, Wayland frame capture integration,... |
| `usr/share/doc/mios/manual/ch85-ocr-credential-masking.md` | Chapter 85: Lightweight On-Device OCR Regex Credential Masking Pipeline for Vision Frames (task_484, task_2717). Details on-device OCR engine integration (Tesseract/PaddleOCR), zero-trust credential... |
| `usr/share/doc/mios/manual/ch86-shadow-candidate-testing.md` | Chapter 86: Shadow Dual-Process Candidate Daemon Validator and Query Mirroring Harness (task_485, task_2718). Covers shadow execution mechanics, query mirroring architecture, mutation guarding,... |
| `usr/share/doc/mios/manual/ch87-socket-activation-handoff.md` | Chapter 87: Zero-Downtime Systemd Socket Handoff (sd_listen_fds) Daemon Swapper for Agent-Pipe (task_486, task_2719). Covers systemd socket activation, file descriptor inheritance, SCM_RIGHTS FD... |
| `usr/share/doc/mios/manual/ch88-proactive-thermal-governor.md` | Chapter 88: Proactive PID Thermal Daemon, Dynamic CPU/GPU Power Cap Modulator, and Thermal Stress Recovery (task_487, task_2720, task_488, task_2721). Details proactive PID thermal regulation, sysfs... |
| `usr/share/doc/mios/manual/cockpit.socket.d.md` | Manual pages distilled from the source comments of cockpit.socket.d, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/conf.d.md` | Manual pages distilled from the source comments of conf.d, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/config.md` | Manual pages distilled from the source comments of config, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/containers.md` | Manual pages distilled from the source comments of containers, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/context.md` | Manual pages distilled from the source comments of context, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/crawl4ai.md` | Manual pages distilled from the source comments of crawl4ai, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/db.md` | Manual pages distilled from the source comments of db, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/deploy.md` | Manual pages distilled from the source comments of deploy, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/diag.md` | Manual pages distilled from the source comments of diag, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/diff.md` | Manual pages distilled from the source comments of diff, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/display.md` | Manual pages distilled from the source comments of display, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/drift.md` | Manual pages distilled from the source comments of drift, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/embeddings.md` | Manual pages distilled from the source comments of embeddings, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/evals.md` | Manual pages distilled from the source comments of evals, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/federation.md` | Manual pages distilled from the source comments of federation, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/firstboot.md` | Manual page for firstboot subsystem |
| `usr/share/doc/mios/manual/gateway-agent.md` | Manual pages distilled from the source comments of gateway-agent, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/git.md` | Manual pages distilled from the source comments of git, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/graph.md` | Manual pages distilled from the source comments of graph, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/harness.md` | Manual pages distilled from the source comments of harness, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/hermes.md` | Manual pages distilled from the source comments of hermes, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/hw.md` | Manual pages distilled from the source comments of hw, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/identity.md` | Manual pages distilled from the source comments of identity, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/installation.md` | Manual pages distilled from the source comments of installation, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/invariants.md` | Manual pages distilled from the source comments of invariants, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/ipc.md` | Manual pages distilled from the source comments of ipc, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/kargs.d.md` | Manual pages distilled from the source comments of kargs.d, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/kernel.md` | Manual pages distilled from the source comments of kernel, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/lib.md` | Manual pages distilled from the source comments of lib, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/lifecycle.md` | Manual pages distilled from the source comments of lifecycle, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/llamacpp.md` | Manual pages distilled from the source comments of llamacpp, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/log.md` | Manual pages distilled from the source comments of log, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/mcp.md` | Manual pages distilled from the source comments of mcp, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/memory.md` | Manual pages distilled from the source comments of memory, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/mios.md` | Manual pages distilled from the source comments of mios, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/mios_pipe.md` | Manual pages distilled from the source comments of mios_pipe, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/net.md` | Manual pages distilled from the source comments of net, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/node.md` | Manual pages distilled from the source comments of node, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/observability.md` | Manual pages distilled from the source comments of observability, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/offline-upgrade.md` | Explains signed offline OCI upgrades and persistent host state. |
| `usr/share/doc/mios/manual/opencode-gateway.md` | Manual pages distilled from the source comments of opencode-gateway, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/owui.md` | Manual pages distilled from the source comments of owui, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/powershell.md` | Manual pages distilled from the source comments of powershell, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/profile.d.md` | Manual pages distilled from the source comments of profile.d, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/prompt.md` | Manual pages distilled from the source comments of prompt, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/root.md` | Manual pages distilled from the source comments of root, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/routing.md` | Manual pages distilled from the source comments of routing, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/scheduler.md` | Manual pages distilled from the source comments of scheduler, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/searxng.md` | Manual pages distilled from the source comments of searxng, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/sec.md` | Manual pages distilled from the source comments of sec, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/src.md` | Manual pages distilled from the source comments of src, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/ssot.md` | Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. |
| `usr/share/doc/mios/manual/storage.md` | Manual pages distilled from the source comments of storage, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/support.md` | Manual pages distilled from the source comments of support, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/system.md` | Manual pages distilled from the source comments of system, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/systemd.md` | Manual page for systemd subsystem |
| `usr/share/doc/mios/manual/sysusers.d.md` | Manual pages distilled from the source comments of sysusers.d, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/tests.md` | Manual pages distilled from the source comments of tests, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/thesis.md` | Core thesis, nature, designed vs. observed scope, and architectural pillars for MiOS. |
| `usr/share/doc/mios/manual/tmpfiles.d.md` | Manual pages distilled from the source comments of tmpfiles.d, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/tools.md` | Manual pages distilled from the source comments of tools, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/user.md` | Manual pages distilled from the source comments of user, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/users.md` | Manual pages distilled from the source comments of users, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/ux.md` | Manual pages distilled from the source comments of ux, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/virt.md` | Manual pages distilled from the source comments of virt, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/win.md` | Manual pages distilled from the source comments of win, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/windows.md` | Manual pages distilled from the source comments of windows, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/workflows.md` | Manual pages distilled from the source comments of workflows, sanitized, each passage anchored to the comment it came from. |
| `usr/share/doc/mios/manual/xdg-desktop-portal.md` | Manual pages distilled from the source comments of xdg-desktop-portal, sanitized, each passage anchored to the comment it came from. |

<!-- derived from the AI-hint headers of 203 file(s) matching usr/share/doc/mios/manual/*.md -->
<!-- /MIOS-GEN:index:usr/share/doc/mios/manual/*.md -->
