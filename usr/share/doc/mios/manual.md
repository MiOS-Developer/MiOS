<!-- AI-hint: Entry point of the MiOS All-in-One manual: intro, full table of contents and the generated chapter index; every chapter is an authored file under manual/. -->
# MiOS All-in-One User Manual & System Documentation

Welcome to the comprehensive, All-in-One User Manual and System Documentation for **MiOS** (pronounced *"MyOS"*).

This manual assembles the 89-chapter documentation suite. Each chapter is an authored file under [`manual/`](manual/); this page carries the table of contents and the machine-checked chapter index.

---

## Table of Contents

### Part I: Foundations & Philosophy
* **[Chapter 01: Introduction and Core Concepts](manual/ch01-introduction-and-core-concepts.md)**
  * [What is MiOS](manual/ch01-introduction-and-core-concepts.md#01_what_is_mios): Defines the dual nature of MiOS as an immutable, bootc Fedora workstation and a local agentic OS.
  * [Repo IS Root Paradigm](manual/ch01-introduction-and-core-concepts.md#01_repo_is_root_paradigm): Explains how the Git repository tree directly mirrors the deployed OS filesystem at the system root.
  * [The Architectural Laws](manual/ch01-introduction-and-core-concepts.md#01_the_seven_architectural_laws): Details the non-negotiable mandates: USR-OVER-ETC, NO-MKDIR-IN-VAR, BOUND-IMAGES, etc.
* **[Chapter 02: Installation and Deployment](manual/ch02-installation-and-deployment.md)**
  * [Day-0 Bootstrap](manual/ch02-installation-and-deployment.md#02_day_0_bootstrap): Covers provisioning the MiOS-DEV seed environment via Windows PowerShell or the Linux just runner.
  * [First Boot Initialization](manual/ch02-installation-and-deployment.md#02_first_boot_initialization): Outlines the provisioning sequence for the build plane, CDI, libvirt, and AI plane on first boot.
  * [Day-N Self-Replication](manual/ch02-installation-and-deployment.md#02_day_n_self_replication): Details the continuous CI/CD loop where a running MiOS host builds and updates its own OCI images.
  * [Deployment Targets](manual/ch02-installation-and-deployment.md#02_deployment_targets): Provides recipes for deploying the MiOS image to bare-metal hosts, VHDX, RAW, WSL2, and ISO.
* **[Chapter 03: System Configuration and Governance](manual/ch03-system-configuration-and-governance.md)**
  * [Single Source of Truth](manual/ch03-system-configuration-and-governance.md#03_single_source_of_truth): Explains the management of packages, AI lanes, and quadlets centrally via mios.toml.
  * [Three-Layer Override Model](manual/ch03-system-configuration-and-governance.md#03_three_layer_override_model): Maps configuration resolution precedence across vendor, host, and user levels.
  * [Declarative Package Management](manual/ch03-system-configuration-and-governance.md#03_declarative_package_management): Documents DNF5 integration, flatpak configurations, and the separation of PACKAGES.md.

### Part II: The Agentic AI Stack
* **[Chapter 04: The Agentic AI Stack](manual/ch04-the-agentic-ai-stack.md)**
  * [Unified AI Endpoint](manual/ch04-the-agentic-ai-stack.md#04_unified_ai_endpoint): Describes the routing of all AI interactions through the MIOS_AI_ENDPOINT (Hermes gateway, port key `hermes`).
  * [Agent Pipe Orchestrator](manual/ch04-the-agentic-ai-stack.md#04_agent_pipe_orchestrator): Details the primary front door on the `agent_pipe` port used to route requests and fan out tasks.
  * [MiOS Hermes Gateway](manual/ch04-the-agentic-ai-stack.md#04_mios_hermes_gateway): Outlines the operation of the tool-loop gateway and session manager running on the `hermes` port.
  * [Inference Lanes](manual/ch04-the-agentic-ai-stack.md#04_inference_lanes): Maps the local token generation engines, llama.cpp proxy, and VRAM-gated heavy lanes.
  * [Unified Agent Memory](manual/ch04-the-agentic-ai-stack.md#04_unified_agent_memory): Covers episodic and long-term knowledge storage using PostgreSQL and pgvector.
* **[Chapter 05: Federation and Computer Use](manual/ch05-federation-and-computer-use.md)**
  * [Model Context Protocol](manual/ch05-federation-and-computer-use.md#05_model_context_protocol): Details the standardized MCP interface utilized by agents to discover external tools.
  * [Agent-to-Agent Delegation](manual/ch05-federation-and-computer-use.md#05_agent_to_agent_delegation): Documents the A2A JSON-RPC specifications for peer delegation.
  * [Vision and OS Control](manual/ch05-federation-and-computer-use.md#05_vision_and_os_control): Explains Wayland automation, vision grounding via UI-TARS, and pc-control tools.
* **[Chapter 06: Security and Hardware Virtualization](manual/ch06-security-and-hardware-virtualization.md)**
  * [Immutable Root and Integrity](manual/ch06-security-and-hardware-virtualization.md#06_immutable_root_and_integrity): Explains composefs sealing of the read-only /usr directory and fs-verity.
  * [Runtime Guards](manual/ch06-security-and-hardware-virtualization.md#06_runtime_guards): Details defense-in-depth mechanisms via CrowdSec, fapolicyd, and USBGuard.
  * [Keyless Image Signing](manual/ch06-security-and-hardware-virtualization.md#06_keyless_image_signing): Covers OCI validation and authentication via Sigstore and cosign.
  * [Unprivileged Quadlet Model](manual/ch06-security-and-hardware-virtualization.md#06_unprivileged_quadlet_model): Documents user permission tiers required to execute services via rootless Podman.
  * [Hardware Passthrough](manual/ch06-security-and-hardware-virtualization.md#06_hardware_passthrough): Maps GPU exposure to virtual machines and containers via VFIO-PCI and CDI.
* **[Chapter 07: Cluster and Storage Fabric](manual/ch07-cluster-and-storage-fabric.md)**
  * [K3s Kubernetes Integration](manual/ch07-cluster-and-storage-fabric.md#07_k3s_kubernetes_integration): Outlines the mechanisms for expanding the workstation into a Kubernetes cluster.
  * [Ceph Distributed Storage](manual/ch07-cluster-and-storage-fabric.md#07_ceph_distributed_storage): Explains CephFS containerized storage deployments and privileged exemptions.

### Part III: Core OS Infrastructure
* **[Chapter 08: Bootloader and Unified Kernel Images (UKI)](manual/ch08-bootloader-and-unified-kernel-images-uki.md)**
  * [UKI Layout and Baking](manual/ch08-bootloader-and-unified-kernel-images-uki.md#08_uki_layout_and_baking): Covers compilation and structure of Unified Kernel Images via systemd-ukify.
  * [Secure Boot Integrity](manual/ch08-bootloader-and-unified-kernel-images-uki.md#08_secure_boot_integrity): Details kernel module signing, trust models, and cryptographic verification chains.
  * [Kernel Arguments and Gating](manual/ch08-bootloader-and-unified-kernel-images-uki.md#08_kernel_arguments_and_gating): Explains static kernel arguments in kargs.d mapping to VM and GPU isolation.
* **[Chapter 09: Systemd and Quadlet Orchestration](manual/ch09-systemd-and-quadlet-orchestration.md)**
  * [Unprivileged Systemd Tiers](manual/ch09-systemd-and-quadlet-orchestration.md#09_unprivileged_systemd_tiers): Defines user-space daemon layers and systemd-generator permissions configuration.
  * [Quadlet Configuration Syntax](manual/ch09-systemd-and-quadlet-orchestration.md#09_quadlet_configuration_syntax): Explains how podman quadlets render systemd unit files on startup.
  * [Dynamic Service Activation](manual/ch09-systemd-and-quadlet-orchestration.md#09_dynamic_service_activation): Details service lifecycle states triggered by sync-env or user edits.

### Part IV: Detailed Inference & Execution Layers
* **[Chapter 10: Local Inference Lanes and llama.cpp](manual/ch10-local-inference-lanes-and-llama-cpp.md)**
  * [Llama-Swap Proxy Architecture](manual/ch10-local-inference-lanes-and-llama-cpp.md#10_llama_swap_proxy_architecture): Covers how llama-swap handles hot swapping and KV paging on the `llm_light` port.
  * [Embedded Inference Setup](manual/ch10-local-inference-lanes-and-llama-cpp.md#10_embedded_inference_setup): Maps GPU context management, prompt template bindings, and model formats.
  * [Model Map and Hot Swapping](manual/ch10-local-inference-lanes-and-llama-cpp.md#10_model_map_and_hot_swapping): Documents model map configuration file and resource optimization strategies.
* **[Chapter 11: The Heavy GPU Lane and its Engines (vLLM or SGLang)](manual/ch11-heavy-gpu-lanes-and-sglang-vllm.md)**
  * [One Heavy Lane, Two Engines](manual/ch11-heavy-gpu-lanes-and-sglang-vllm.md#11_one_heavy_lane_two_engines): Explains the shared heavy lane, port and route for either engine.
  * [Engine Options](manual/ch11-heavy-gpu-lanes-and-sglang-vllm.md#11_engine_options): Describes how the layered configuration selects vLLM or SGLang.
  * [VRAM Allocation and Scheduling](manual/ch11-heavy-gpu-lanes-and-sglang-vllm.md#11_vram_allocation_and_scheduling): Covers pre-allocation thresholds and dynamic offloading policies.
* **[Chapter 12: Unified Memory and pgvector Schema](manual/ch12-unified-memory-and-pgvector-schema.md)**
  * [PostgreSQL Integration](manual/ch12-unified-memory-and-pgvector-schema.md#12_postgresql_integration): Details pgvector database container setup, connection pools, and permissions.
  * [Semantic Knowledge Recall](manual/ch12-unified-memory-and-pgvector-schema.md#12_semantic_knowledge_recall): Explains cosine-similarity searches utilizing vector retrieval.
  * [Epistemic Memory Pruning](manual/ch12-unified-memory-and-pgvector-schema.md#12_epistemic_memory_pruning): Covers background archival workers and semantic consolidation.
* **[Chapter 13: Model Context Protocol Integration](manual/ch13-model-context-protocol-integration.md)**
  * [Custom MCP Server Design](manual/ch13-model-context-protocol-integration.md#13_custom_mcp_server_design): Describes how to write custom Python or Go MCP servers.
  * [Tool Discovery Protocols](manual/ch13-model-context-protocol-integration.md#13_tool_discovery_protocols): Covers how the AI gateway queries the system tool registry.
  * [Security Sandboxing for MCP](manual/ch13-model-context-protocol-integration.md#13_security_sandboxing_for_mcp): Details how tools run in sandboxed namespaces to prevent host escapes.
* **[Chapter 14: Agent-to-Agent Delegation Protocols](manual/ch14-agent-to-agent-delegation-protocols.md)**
  * [JSON-RPC Delegation Specification](manual/ch14-agent-to-agent-delegation-protocols.md#14_json_rpc_delegation_specification): Details the communications standard and payload schema for agent delegation.
  * [OpenCode Specialist Handoffs](manual/ch14-agent-to-agent-delegation-protocols.md#14_opencode_specialist_handoffs): Explains how the coding subagent (MiOS-OpenCode) takes over code modification.
  * [Peer-to-Peer Trust Models](manual/ch14-agent-to-agent-delegation-protocols.md#14_peer_to_peer_trust_models): Defines the capability-based security mapping across cooperative agents.
* **[Chapter 15: Computer Use and Desktop Control](manual/ch15-computer-use-and-desktop-control.md)**
  * [UI-TARS Vision Grounding](manual/ch15-computer-use-and-desktop-control.md#15_ui_tars_vision_grounding): Details coordinate grounding on Wayland screens via vision models.
  * [Wayland Input Automation](manual/ch15-computer-use-and-desktop-control.md#15_wayland_input_automation): Explains input emulation via the mios-pc-control command suite.
  * [AT-SPI Accessibility Tuning](manual/ch15-computer-use-and-desktop-control.md#15_at_spi_accessibility_tuning): Documents screen tree traversal for structural UI reasoning.

* **[Chapter 16: Immutable Root and Composefs Sealing](manual/ch16-immutable-root-and-composefs-sealing.md)**
  * [Composefs Read-Only Mounts](manual/ch16-immutable-root-and-composefs-sealing.md#16_composefs_read_only_mounts): Explains composefs structures and /usr partition read-only mounts.
  * [fs-verity Signature Verification](manual/ch16-immutable-root-and-composefs-sealing.md#16_fs_verity_signature_verification): Covers system file validation against trusted cryptographic hashes.
  * [Host Upgrade Reconciliation](manual/ch16-immutable-root-and-composefs-sealing.md#16_host_upgrade_reconciliation): Describes how upgrades resolve changes between base and current states.
* **[Chapter 17: Defense in Depth Hardening](manual/ch17-defense-in-depth-hardening.md)**
  * [CrowdSec Intrusion Prevention](manual/ch17-defense-in-depth-hardening.md#17_crowdsec_intrusion_prevention): Covers telemetry monitoring, IP bans, and custom local parsers.
  * [fapolicyd Application Whitelisting](manual/ch17-defense-in-depth-hardening.md#17_fapolicyd_application_whitelisting): Details binary execution blocking on unauthorized directories.
  * [USBGuard Hardware Control](manual/ch17-defense-in-depth-hardening.md#17_usbguard_hardware_control): Explains protection policies against rogue USB devices.
* **[Chapter 18: Supply Chain and Image Integrity](manual/ch18-supply-chain-and-image-integrity.md)**
  * [Sigstore Verification Policies](manual/ch18-supply-chain-and-image-integrity.md#18_sigstore_verification_policies): Defines policy-based verification of OCI signatures at pull time.
  * [Keyless Cosign Signing](manual/ch18-supply-chain-and-image-integrity.md#18_keyless_cosign_signing): Covers keyless image signing using OIDC identity providers.
  * [Build-Time Attestation](manual/ch18-supply-chain-and-image-integrity.md#18_build_time_attestation): Explains the generation and verification of build SBOMs.
* **[Chapter 19: Hardware Passthrough and VFIO-PCI](manual/ch19-hardware-passthrough-and-vfio-pci.md)**
  * [GPU Isolation via VFIO](manual/ch19-hardware-passthrough-and-vfio-pci.md#19_gpu_isolation_via_vfio): Details binding GPUs to vfio-pci on boot, bypassing host drivers.
  * [Libvirt PCI Routing](manual/ch19-hardware-passthrough-and-vfio-pci.md#19_libvirt_pci_routing): Explains the XML schema mapping for physical GPU passthrough to guests.
  * [Guest Drivers Enforcement](manual/ch19-hardware-passthrough-and-vfio-pci.md#19_guest_drivers_enforcement): Documents driver setups in guest OS to avoid error codes.
* **[Chapter 20: Container Device Interface Plumbing](manual/ch20-container-device-interface-plumbing.md)**
  * [Nvidia CDI Automation](manual/ch20-container-device-interface-plumbing.md#20_nvidia_cdi_automation): Covers CDI spec generation for CUDA applications running in rootless podman.
  * [AMD ROCm CDI Mappings](manual/ch20-container-device-interface-plumbing.md#20_amd_rocm_cdi_mappings): Explains ROCm/KFD driver mounts and container bindings.
  * [Intel GPU CDI Specs](manual/ch20-container-device-interface-plumbing.md#20_intel_gpu_cdi_specs): Documents Intel graphics acceleration CDI specs.
* **[Chapter 21: Looking Glass B7 and KVMFR](manual/ch21-looking-glass-b7-and-kvmfr.md)**
  * [KVMFR Kernel Module Bake](manual/ch21-looking-glass-b7-and-kvmfr.md#21_kvmfr_kernel_module_bake): Explains building and signing KVMFR module from source.
  * [Shared Memory Framebuffer](manual/ch21-looking-glass-b7-and-kvmfr.md#21_shared_memory_framebuffer): Details allocations under /dev/shm for low-latency memory copy.
  * [Looking Glass Client Setup](manual/ch21-looking-glass-b7-and-kvmfr.md#21_looking_glass_client_setup): Documents Wayland client build and input mappings.
* **[Chapter 22: CPU Topology and Performance Pinning](manual/ch22-cpu-topology-and-performance-pinning.md)**
  * [Thread Allocation Strategies](manual/ch22-cpu-topology-and-performance-pinning.md#22_thread_allocation_strategies): Maps CPU pinning allocations for isolated workloads.
  * [NUMA Node Awareness](manual/ch22-cpu-topology-and-performance-pinning.md#22_numa_node_awareness): Details memory node alignment for reduced guest latencies.
  * [Low-Latency VM Tuning](manual/ch22-cpu-topology-and-performance-pinning.md#22_low_latency_vm_tuning): Covers scheduling priority and emulatorpin adjustments.

* **[Chapter 23: Single-Node Kubernetes Expansion](manual/ch23-single-node-kubernetes-expansion.md)**
  * [K3s Workstation Coexistence](manual/ch23-single-node-kubernetes-expansion.md#23_k3s_workstation_coexistence): Covers resource boundaries between GNOME and K3s services.
  * [Local Ingress and Routing](manual/ch23-single-node-kubernetes-expansion.md#23_local_ingress_and_routing): Details ingress routing rules in single-node clusters.
  * [K3s SELinux Policy Enforcement](manual/ch23-single-node-kubernetes-expansion.md#23_k3s_selinux_policy_enforcement): Explains custom security policies allowing cluster containers.
* **[Chapter 24: CephFS Local Storage Cluster](manual/ch24-cephfs-local-storage-cluster.md)**
  * [Containerized Ceph Deployments](manual/ch24-cephfs-local-storage-cluster.md#24_containerized_ceph_deployments): Covers Ceph Quadlet definitions and storage config.
  * [Storage Daemon Permissions](manual/ch24-cephfs-local-storage-cluster.md#24_storage_daemon_permissions): Details block device access exemptions.
  * [XDG Directory Integrations](manual/ch24-cephfs-local-storage-cluster.md#24_xdg_directory_integrations): Maps user directories onto CephFS mounts for auto-backups.
* **[Chapter 25: Local Search Engine and SearXNG](manual/ch25-local-search-engine-and-searxng.md)**
  * [SearXNG Sovereign Search](manual/ch25-local-search-engine-and-searxng.md#25_searxng_sovereign_search): Explains local container setup and engines configuration.
  * [Agent Search API Plumbing](manual/ch25-local-search-engine-and-searxng.md#25_agent_search_api_plumbing): Covers query routing from search tools to SearXNG.
  * [Web Scraping and Ingest](manual/ch25-local-search-engine-and-searxng.md#25_web_scraping_and_ingest): Details parsing HTML results into Markdown for LLM ingestion.
* **[Chapter 26: Unified Knowledge Base Ingestion](manual/ch26-unified-knowledge-base-ingestion.md)**
  * [Document Parsing and Embedding](manual/ch26-unified-knowledge-base-ingestion.md#26_document_parsing_and_embedding): Explains document indexing and embedding tasks.
  * [Ingest Pipeline Schema](manual/ch26-unified-knowledge-base-ingestion.md#26_ingest_pipeline_schema): Maps ingestion pipeline and database tables layout.
  * [Semantic Indexing Maintenance](manual/ch26-unified-knowledge-base-ingestion.md#26_semantic_indexing_maintenance): Covers re-indexing databases and recall optimizations.
* **[Chapter 27: Shell Configuration and Environment Cascade](manual/ch27-shell-configuration-and-environment-cascade.md)**
  * [Environment Defaults and Precedence](manual/ch27-shell-configuration-and-environment-cascade.md#27_environment_defaults_and_precedence): Maps configuration overrides bubbling up to login shells.
  * [Oh My Posh Prompt Theming](manual/ch27-shell-configuration-and-environment-cascade.md#27_oh_my_posh_prompt_theming): Covers theme configuration and prompt status icons.
  * [User Locale Standardization](manual/ch27-shell-configuration-and-environment-cascade.md#27_user_locale_standardization): Documents timezone and UTF-8 locale staging setups.
* **[Chapter 28: Dynamic Network and Firewall Management](manual/ch28-dynamic-network-and-firewall-management.md)**
  * [Firewalld Rule Generation](manual/ch28-dynamic-network-and-firewall-management.md#28_firewalld_rule_generation): Covers managing port firewalls via firewalld command hooks.
  * [Dynamic Port Allocation](manual/ch28-dynamic-network-and-firewall-management.md#28_dynamic_port_allocation): Explains how ports are dynamically resolved and bound.
  * [VPN and Tailscale Routing](manual/ch28-dynamic-network-and-firewall-management.md#28_vpn_and_tailscale_routing): Documents Tailscale integration with system firewall rules.
* **[Chapter 29: Web Management and Configurator UI](manual/ch29-web-management-and-configurator-ui.md)**
  * [MiOS HTML TOML Editor](manual/ch29-web-management-and-configurator-ui.md#29_mios_html_toml_editor): Covers configuration editing via the static index HTML form.
  * [Host-to-Container Portal](manual/ch29-web-management-and-configurator-ui.md#29_host_to_container_portal): Details how the UI panel maps active container metrics.
  * [Settings Sync Mechanisms](manual/ch29-web-management-and-configurator-ui.md#29_settings_sync_mechanisms): Explains TOML serialization and service reload hooks.

* **[Chapter 30: System Auditing and Drift Verification](manual/ch30-system-auditing-and-drift-verification.md)**
  * [Automated Postcheck Suite](manual/ch30-system-auditing-and-drift-verification.md#30_automated_postcheck_suite): Documents checks run by 99-postcheck.sh at build-time.
  * [Hardcode Lint Rules](manual/ch30-system-auditing-and-drift-verification.md#30_hardcode_lint_rules): Explains build constraints blocking hardcoded URLs or ports.
  * [Security Policy Compliance](manual/ch30-system-auditing-and-drift-verification.md#30_security_policy_compliance): Maps validation against our target zero-trust hardening profile.
* **[Chapter 31: Desktop Applications and Flatpaks](manual/ch31-desktop-applications-and-flatpaks.md)**
  * [Declarative Flatpak Bake](manual/ch31-desktop-applications-and-flatpaks.md#31_declarative_flatpak_bake): Covers pre-downloading and staging Flatpaks inside the image.
  * [Application Permissions Gating](manual/ch31-desktop-applications-and-flatpaks.md#31_application_permissions_gating): Explains locking Flatpak permissions using Flatseal overrides.
  * [Desktop Shortcuts Sync](manual/ch31-desktop-applications-and-flatpaks.md#31_desktop_shortcuts_sync): Details sync hooks registering menus and MIME shortcuts.
* **[Chapter 32: Swarm Worker Clusters](manual/ch32-swarm-worker-clusters.md)**
  * [Swarm Node Provisioning](manual/ch32-swarm-worker-clusters.md#32_swarm_node_provisioning): Covers dynamic worker provisioning via Quadlet templates.
  * [Dynamic Fanout Orchestration](manual/ch32-swarm-worker-clusters.md#32_dynamic_fanout_orchestration): Details task partitioning and worker aggregation pipelines.
  * [Load Balancing Lanes](manual/ch32-swarm-worker-clusters.md#32_load_balancing_lanes): Explains scheduling and routing algorithms across worker processes.
* **[Chapter 33: Sandboxed Execution and Coder Sandbox](manual/ch33-sandboxed-execution-and-coder-sandbox.md)**
  * [Coder Sandbox Quadlet](manual/ch33-sandboxed-execution-and-coder-sandbox.md#33_coder_sandbox_quadlet): Covers configuring unprivileged containers for code interpretation.
  * [SELinux Sandbox Policies](manual/ch33-sandboxed-execution-and-coder-sandbox.md#33_selinux_sandbox_policies): Details how policies restrict container sandbox processes.
  * [Safe Code Interpretation](manual/ch33-sandboxed-execution-and-coder-sandbox.md#33_safe_code_interpretation): Explains output validation and script execution controls.
* **[Chapter 34: Identity Management and FreeIPA](manual/ch34-identity-management-and-freeipa.md)**
  * [FreeIPA Client Configuration](manual/ch34-identity-management-and-freeipa.md#34_freeipa_client_configuration): Covers configuring FreeIPA libraries inside Fedora overlay.
  * [Enforced User Sysusers](manual/ch34-identity-management-and-freeipa.md#34_enforced_user_sysusers): Details staging user and system accounts prior to install.
  * [Domain Join Automation](manual/ch34-identity-management-and-freeipa.md#34_domain_join_automation): Explains automatic domain enrollment on first boot.
* **[Chapter 35: System Monitoring and Telemetry](manual/ch35-system-monitoring-and-telemetry.md)**
  * [Prometheus Exporter Setup](manual/ch35-system-monitoring-and-telemetry.md#35_prometheus_exporter_setup): Covers collecting CPU, RAM, and GPU stats via node-exporters.
  * [AI Gateway Telemetry](manual/ch35-system-monitoring-and-telemetry.md#35_ai_gateway_telemetry): Details tracking query duration, tokens, and routing lanes.
  * [Grafana_Dashboard_Profiles](manual/ch35-system-monitoring-and-telemetry.md#35_grafana_dashboard_profiles): Maps visual dashboards for monitoring resource use.
* **[Chapter 36: Greenboot Health Check and Recovery](manual/ch36-greenboot-health-check-and-recovery.md)**
  * [Automatic OS Health Checks](manual/ch36-greenboot-health-check-and-recovery.md#36_automatic_os_health_checks): Covers greenboot scripts verifying service states.
  * [Rollback Trigger Policies](manual/ch36-greenboot-health-check-and-recovery.md#36_rollback_trigger_policies): Explains atomic image swap checks triggered on boot failures.
  * [Recovery State Scripts](manual/ch36-greenboot-health-check-and-recovery.md#36_recovery_state_scripts): Documents dynamic cleanup tasks executed during recoveries.
* **[Chapter 37: GPU Capability Detection and Passthrough Shims](manual/ch37-gpu-capability-detection-and-passthrough-shims.md)**
  * [CDI Refresh Mechanisms](manual/ch37-gpu-capability-detection-and-passthrough-shims.md#37_cdi_refresh_mechanisms): Covers spec updates triggered when hardware states change.
  * [Runtime GPU Gating](manual/ch37-gpu-capability-detection-and-passthrough-shims.md#37_runtime_gpu_gating): Details device locking and lockouts during state transitions.
  * [Dynamic Driver Loading](manual/ch37-gpu-capability-detection-and-passthrough-shims.md#37_dynamic_driver_loading): Explains dynamic module load decisions during bootstrap.
* **[Chapter 38: Remote Desktop and GNOME GRD](manual/ch38-remote-desktop-and-gnome-grd.md)**
  * [Remote Wayland Sessions](manual/ch38-remote-desktop-and-gnome-grd.md#38_remote_wayland_sessions): Covers running GNOME inside headless Wayland sessions.
  * [Secure RDP Authentication](manual/ch38-remote-desktop-and-gnome-grd.md#38_secure_rdp_authentication): Details TLS encryption and user credential checks.
  * [Headless Desktop Toggle](manual/ch38-remote-desktop-and-gnome-grd.md#38_headless_desktop_toggle): Documents setting up virtual display outputs on headless hosts.
* **[Chapter 39: Host-Guest Shared Filesystems](manual/ch39-host-guest-shared-filesystems.md)**
  * [Virtiofs Performance Tuning](manual/ch39-host-guest-shared-filesystems.md#39_virtiofs_performance_tuning): Covers high-speed file sharing cache configurations.
  * [Shared Directories Overlay](manual/ch39-host-guest-shared-filesystems.md#39_shared_directories_overlay): Details exposing system paths inside guest virtual overlays.
  * [Permission Translation Models](manual/ch39-host-guest-shared-filesystems.md#39_permission_translation_models): Explains UID/GID mappings translation across OS barriers.
* **[Chapter 40: System Log Aggregation](manual/ch40-system-log-aggregation.md)**
  * [Journald Sync to Bootstrap](manual/ch40-system-log-aggregation.md#40_journald_sync_to_bootstrap): Covers sync hooks pulling logs into bootstrap sectors.
  * [Log-Copy Daemon Configuration](manual/ch40-system-log-aggregation.md#40_log_copy_daemon_configuration): Details systemd service parameters for log copy tasks.
  * [Diagnostic Log Bundles](manual/ch40-system-log-aggregation.md#40_diagnostic_log_bundles): Explains compiling system diagnostics into single archives.
* **[Chapter 41: Machine Owner Key Management](manual/ch41-machine-owner-key-management.md)**
  * [Private Key Generation](manual/ch41-machine-owner-key-management.md#41_private_key_generation): Covers generating secure build-keys inside automation.
  * [Secure Boot Enrollment Flow](manual/ch41-machine-owner-key-management.md#41_secure_boot_enrollment_flow): Details UEFI enrollment prompts triggered on boots.
  * [Automatic Module Signing](manual/ch41-machine-owner-key-management.md#41_automatic_module_signing): Explains dynamic module signatures added on kernel upgrades.
* **[Chapter 42: Kernel Upgrade and Build Pipelines](manual/ch42-kernel-upgrade-and-build-pipelines.md)**
  * [Stable LTS Kernel Updates](manual/ch42-kernel-upgrade-and-build-pipelines.md#42_stable_lts_kernel_updates): Covers base image upgrades and validation procedures.
  * [Akmod Compilation Guards](manual/ch42-kernel-upgrade-and-build-pipelines.md#42_akmod_compilation_guards): Details compilation gating rules verifying module states.
  * [BIB Disk Image Generation](manual/ch42-kernel-upgrade-and-build-pipelines.md#42_bib_disk_image_generation): Explains bootc-image-builder actions transforming OCI tags.
* **[Chapter 43: Local Registry and OCI Distribution](manual/ch43-local-registry-and-oci-distribution.md)**
  * [Private Registry Quadlets](manual/ch43-local-registry-and-oci-distribution.md#43_private_registry_quadlets): Covers OCI distribution containers used in replication loop.
  * [Image Caching Strategies](manual/ch43-local-registry-and-oci-distribution.md#43_image_caching_strategies): Details cache boundaries speeding up successive image builds.
  * [Deployed Ref Updates](manual/ch43-local-registry-and-oci-distribution.md#43_deployed_ref_updates): Explains pulling local registries and switching host roots.
* **[Chapter 44: Host Package Overrides and DNF5](manual/ch44-host-package-overrides-and-dnf5.md)**
  * [USR vs ETC Overrides](manual/ch44-host-package-overrides-and-dnf5.md#44_usr_vs_etc_overrides): Covers configurations prioritization mappings.
  * [RPM-OSTree Exemptions](manual/ch44-host-package-overrides-and-dnf5.md#44_rpm_ostree_exemptions): Details manual package installations resolving hardware conflicts.
  * [Dependency Conflict Resolution](manual/ch44-host-package-overrides-and-dnf5.md#44_dependency_conflict_resolution): Explains troubleshooting procedures for dnf packages errors.
* **[Chapter 45: Diagnostic Tools and Profilers](manual/ch45-diagnostic-tools-and-profilers.md)**
  * [Hardware Capability Profiling](manual/ch45-diagnostic-tools-and-profilers.md#45_hardware_capability_profiling): Covers physical adapter checks run by system-profilers.
  * [Egress Firewall Verification](manual/ch45-diagnostic-tools-and-profilers.md#45_egress_firewall_verification): Details checks verifying container loopback containment.
  * [Profile Comparison Utilities](manual/ch45-diagnostic-tools-and-profilers.md#45_profile_comparison_utilities): Explains comparing active setups against templates.
* **[Chapter 46: User Persona Staging](manual/ch46-user-persona-staging.md)**
  * [Default User Creation](manual/ch46-user-persona-staging.md#46_default_user_creation): Covers default accounts, credentials, and settings groups.
  * [Stagings Dotfiles Overlay](manual/ch46-user-persona-staging.md#46_stagings_dotfiles_overlay): Details template overlay merging home profile files.
  * [Multi-User Sandboxes](manual/ch46-user-persona-staging.md#46_multi_user_sandboxes): Explains isolation policies across different accounts.
* **[Chapter 47: Virtual Machine Templates](manual/ch47-virtual-machine-templates.md)**
  * [Windows 11 SecureBoot XML](manual/ch47-virtual-machine-templates.md#47_windows_11_secureboot_xml): Details template variables enabling vTPM and Secure Boot.
  * [Linux Guest Cloud-Init](manual/ch47-virtual-machine-templates.md#47_linux_guest_cloud_init): Covers automating guest staging using init data.
  * [VM Lifecycle Management](manual/ch47-virtual-machine-templates.md#47_vm_lifecycle_management): Explains hypervisor guest actions executed via virsh.
* **[Chapter 48: Local AI Web Consoles](manual/ch48-local-ai-web-consoles.md)**
  * [Open WebUI Deployment](manual/ch48-local-ai-web-consoles.md#48_open_webui_deployment): Covers Open WebUI Quadlet parameters and local mapping.
  * [Interface Customization](manual/ch48-local-ai-web-consoles.md#48_interface_customization): Details interface layout settings and custom models aliases.
  * [Token-based Access Control](manual/ch48-local-ai-web-consoles.md#48_token_based_access_control): Explains console access security using token authentication.
* **[Chapter 49: Offline-First Governance](manual/ch49-offline-first-governance.md)**
  * [Local Package Mirrors](manual/ch49-offline-first-governance.md#49_local_package_mirrors): Covers staging local mirror caches inside container build overlay.
  * [Sovereign Model Storage](manual/ch49-offline-first-governance.md#49_sovereign_model_storage): Details models weights verification loaded under /srv/ai.
  * [Non-Network Degradation Modes](manual/ch49-offline-first-governance.md#49_non_network_degradation_modes): Explains fallback behaviors resolving missing active gateways.
* **[Chapter 50: Upstream Tracking and Maintenance](manual/ch50-upstream-tracking-and-maintenance.md)**
  * [Upstream Drift Monitor](manual/ch50-upstream-tracking-and-maintenance.md#50_upstream_drift_monitor): Covers checking changes between host and remote overlays.
  * [Justfile Pipeline Automation](manual/ch50-upstream-tracking-and-maintenance.md#50_justfile_pipeline_automation): Details Justfile build automation and check goals.
  * [Release Maturity Runbook](manual/ch50-upstream-tracking-and-maintenance.md#50_release_maturity_runbook): Explains checklist targets required to tag release stages.


* **[Chapter 51: Distilled System Knowledge & Code Invariants](manual/ch51-distilled-system-knowledge-code-invariants.md)**
  * [Distilled System Knowledge](manual/ch51-distilled-system-knowledge-code-invariants.md#51_distilled_system_knowledge): Consolidates distilled invariants and recovered technical comments.

* **[Chapter 52: Multi-Judge Consensus](manual/ch52-multi-judge-consensus.md)**
  * [The Weighted Vote Fold](manual/ch52-multi-judge-consensus.md#52_the_weighted_vote_fold): Covers folding per-lane verdicts by weight, and why an abstention is not a rejection.
  * [The Quorum Floor](manual/ch52-multi-judge-consensus.md#52_the_quorum_floor): Details the live-vote minimum below which the panel declines to decide.
  * [Reliability Weighting](manual/ch52-multi-judge-consensus.md#52_reliability_weighting): Explains the three weight tiers and the floor that keeps every lane voting.
  * [Reciprocal-Rank-Fusion](manual/ch52-multi-judge-consensus.md#52_reciprocal_rank_fusion): Covers merging ranked candidate lists across lanes deterministically.
  * [Degrading Open](manual/ch52-multi-judge-consensus.md#52_degrading_open): Details the three ways the panel steps aside for the single-lane judge.
  * [Consensus Configuration](manual/ch52-multi-judge-consensus.md#52_consensus_configuration): Explains the [consensus] SSOT keys, env overrides and lane declarations.

* **[Chapter 53: Drift Monitoring](manual/ch53-drift-monitoring.md)**
  * [The Divergence Measure](manual/ch53-drift-monitoring.md#53_the_divergence_measure): Covers the bounded, symmetric Jensen-Shannon score and why it beats KL here.
  * [The Frozen Baseline](manual/ch53-drift-monitoring.md#53_the_frozen_baseline): Details self-seeding the reference so the alarm starts quiet.
  * [The Thin-Window Guard](manual/ch53-drift-monitoring.md#53_the_thin_window_guard): Explains why a handful of samples can never raise an alert.
  * [Axes and Extraction](manual/ch53-drift-monitoring.md#53_axes_and_extraction): Covers the verdict and intent label extractors over recorded events.
  * [The Alert and the Surface](manual/ch53-drift-monitoring.md#53_the_alert_and_the_surface): Details GET /v1/drift and the drift_alert session event.
  * [Drift Configuration](manual/ch53-drift-monitoring.md#53_drift_configuration): Explains the [drift_monitor] SSOT keys and its split from [drift].

* **[Chapter 54: Agent-Pipe Importability](manual/ch54-agent-pipe-importability.md)**
  * [The Three Undefined Names](manual/ch54-agent-pipe-importability.md#54_the_three_undefined_names): Covers the module-scope names that made server.py unimportable.
  * [Why the Gates Missed It](manual/ch54-agent-pipe-importability.md#54_why_the_gates_missed_it): Details the parse-only lint and the over-broad skip, and how both were closed.
  * [The Four Further Defects](manual/ch54-agent-pipe-importability.md#54_the_four_further_defects): Explains the undefined names found once a real checker ran.
  * [Memory Consolidation](manual/ch54-agent-pipe-importability.md#54_memory_consolidation): Covers the knowledge-table consolidation sweep and its counter-folding rule.
  * [The Module-Size Ratchet](manual/ch54-agent-pipe-importability.md#54_the_module_size_ratchet): Details the recursive size gate and its shrink-only [refactor] register.
  * [The OWUI Entry Point](manual/ch54-agent-pipe-importability.md#54_the_owui_entry_point): Covers four defects in the canonical OWUI pipe and the lint blind spot behind them.
  * [Asking Git for the File Set](manual/ch54-agent-pipe-importability.md#54_asking_git_for_the_file_set): Explains replacing per-directory enumeration with the tracked-file set, and its three deliberate exclusions.

* **[Chapter 55: Dead Schema and Half-Wired Units](manual/ch55-dead-schema-and-half-wired-units.md)**
  * [The Dead-Table Gate](manual/ch55-dead-schema-and-half-wired-units.md#55_the_dead_table_gate): Covers the nine tables with no reader or writer, and the one-letter duplicate among them.
  * [What Counts as a Consumer](manual/ch55-dead-schema-and-half-wired-units.md#55_what_counts_as_a_consumer): Explains why docs, config and generated projections are excluded as evidence.
  * [The Provisioner-Triple Gate](manual/ch55-dead-schema-and-half-wired-units.md#55_the_provisioner_triple_gate): Details the fetcher/unit/preset/tmpfiles checks for first-boot provisioners.

* **[Chapter 56: Persistent Shell Sessions](manual/ch56-persistent-shell-sessions.md)**
  * [The Nonce Framing](manual/ch56-persistent-shell-sessions.md#56_the_nonce_framing): Covers the BEGIN/END sentinels and why output cannot forge completion.
  * [What Only Running It Revealed](manual/ch56-persistent-shell-sessions.md#56_what_only_running_it_revealed): Details the echo, hang and lost-head defects the unit tests could not see.
  * [Session Isolation](manual/ch56-persistent-shell-sessions.md#56_session_isolation): Explains the session-key rules that stop one chat reading another's shell.
  * [Shell Session Configuration](manual/ch56-persistent-shell-sessions.md#56_shell_session_configuration): Lists the [shell_session] keys and the run_in_shell verb.
* **[Chapter 57: PowerShell Object Flattening](manual/ch57-powershell-object-flattening.md)**
  * [The Failure Was Silence](manual/ch57-powershell-object-flattening.md#57_the_failure_was_silence): Covers why an object-returning cmdlet returned a blank line, not noise.
  * [Why the Obvious Fixes Fail](manual/ch57-powershell-object-flattening.md#57_why_the_obvious_fixes_fail): Details the three interception approaches a console-less runspace defeats.
  * [The Wrapper](manual/ch57-powershell-object-flattening.md#57_the_wrapper): Explains the four properties the call form has to hold at once.
  * [The No-Staging Fallback](manual/ch57-powershell-object-flattening.md#57_the_no_staging_fallback): Covers why -EncodedCommand replaces the line-at-a-time stdin reader.
  * [PowerShell Configuration](manual/ch57-powershell-object-flattening.md#57_powershell_configuration): Lists the [powershell] keys and the derived Windows staging path.
  * [Flattening Tests](manual/ch57-powershell-object-flattening.md#57_flattening_tests): Details the stub and live tiers of test-powershell-flatten.sh.
* **[Chapter 58: Roadmap Status Parity](manual/ch58-roadmap-status-parity.md)**
  * [The Drift](manual/ch58-roadmap-status-parity.md#58_the_drift): Covers the 49 rows where TASKS.md disagreed with itself about what was left.
  * [Neither Surface Wins](manual/ch58-roadmap-status-parity.md#58_neither_surface_wins): Explains why each disagreement was settled against the tree, not the prose.
  * [The Gate](manual/ch58-roadmap-status-parity.md#58_the_gate): Details check_tasks_status_parity and why it refuses to pass over an empty set.
* **[Chapter 59: Request Coalescing](manual/ch59-request-coalescing.md)**
  * [What It Is Not For](manual/ch59-request-coalescing.md#59_what_it_is_not_for): Explains why the local lanes are bypassed rather than batched a second time.
  * [One Chokepoint](manual/ch59-request-coalescing.md#59_one_chokepoint): Covers the httpx request hook on the single shared AsyncClient.
  * [Sealing](manual/ch59-request-coalescing.md#59_sealing): Details why a flushed group must close before its event is set.
  * [Off Means Absent](manual/ch59-request-coalescing.md#59_off_means_absent): Explains why the default-off hook is never registered at all.
* **[Chapter 60: Durable Quota](manual/ch60-durable-quota.md)**
  * [What Persists](manual/ch60-durable-quota.md#60_what_persists): Covers why the spend window is stored and the RPM window is thrown away.
  * [Stale Rows](manual/ch60-durable-quota.md#60_stale_rows): Explains why a rolled-over window is refused rather than replayed.
  * [A Synchronous Gate Over an Async Store](manual/ch60-durable-quota.md#60_sync_gate_async_store): Details the startup preload and the fire-and-forget write-through.
  * [Degrade Open](manual/ch60-durable-quota.md#60_degrade_open): Explains why an unreachable ledger must never block work.
* **[Chapter 61: Run-Template Replay](manual/ch61-run-template-replay.md)**
  * [The Wrong Key](manual/ch61-run-template-replay.md#61_the_wrong_key): Explains why a plan-shape hash can never decide whether to plan.
  * [Model-Free On Purpose](manual/ch61-run-template-replay.md#61_model_free): Covers why matching is lexical rather than embedded.
  * [Refuse the Near Miss](manual/ch61-run-template-replay.md#61_refuse_the_near_miss): Details the threshold rule and the empty-set score.
  * [The Stamp](manual/ch61-run-template-replay.md#61_the_stamp): Records the one line without which every stored template is unreplayable.
* **[Chapter 62: Sandbox Seccomp](manual/ch62-sandbox-seccomp.md)**
  * [No libseccomp](manual/ch62-sandbox-seccomp.md#62_no_libseccomp): Covers the classic-BPF filter assembled in pure stdlib.
  * [The Number Table Is ABI, Not Trust](manual/ch62-sandbox-seccomp.md#62_abi_not_trust): Explains why syscall numbers are cross-checked against kernel headers.
  * [A Filter That Denies Nothing Is Worse Than None](manual/ch62-sandbox-seccomp.md#62_nothing_is_worse): Details the three refusals and the wrapper's exit-126 stance.
  * [Environment vs Artifact](manual/ch62-sandbox-seccomp.md#62_environment_vs_artifact): Explains what a two-tier test may skip on and what it may never skip.
  * [Two Argv Builders, One Executor](manual/ch62-sandbox-seccomp.md#62_two_argv_builders): Records the divergence left for task_253 rather than changed blind.
* **[Chapter 63: Diff Snapshotting, Boot-Cycle Accrual & HITL Image Roll-in Pipeline](manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md)**
  * [Pre-Poweroff Snapshot](manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md#63_pre_poweroff_snapshot): Captures uncommitted filesystem and git diffs before power-off.
  * [Startup Diff Accrual](manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md#63_startup_diff_accrual): Classifies accrued diffs into safe additive vs high-risk tiers.
  * [Interactive HITL Review](manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md#63_interactive_hitl_review): Quickshell UI and CLI diff auditing workflows.
  * [Autonomous Bake and Greenboot](manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md#63_autonomous_bake_and_greenboot): Autonomous OCI image synthesis and regression rollback.
* **[Chapter 64: AIOS Engine Preemptive Scheduling, Priority Gating & DCI Structured Deliberation](manual/ch64-engine-preemptive-scheduling-and-dci-deliberation.md)**
  * [Priority Scheduling Preemption](manual/ch64-engine-preemptive-scheduling-and-dci-deliberation.md#64_priority_scheduling_preemption): Token-boundary preemption and KV slot paging.
  * [Consequentiality-Gated DCI](manual/ch64-engine-preemptive-scheduling-and-dci-deliberation.md#64_consequentiality_gated_dci): 4-agent structured deliberation with tension tracking.
  * [Progressive Retrieval and LOO](manual/ch64-engine-preemptive-scheduling-and-dci-deliberation.md#64_progressive_retrieval_loo): Manifest-guided search and IntrospecLOO reputation scoring.
* **[Chapter 65: Edge Micro-Mesh 16-Byte Wire Protocol, Dual-Tier Sandboxing & Hierarchical Work-Stealing](manual/ch65-edge-micro-mesh-binary-wire-protocol-and-sandboxing.md)**
  * [Binary Wire Protocol](manual/ch65-edge-micro-mesh-binary-wire-protocol-and-sandboxing.md#65_binary_wire_protocol): 16-byte binary framing with ChaCha20-Poly1305 encryption.
  * [Dual-Tier Sandboxing](manual/ch65-edge-micro-mesh-binary-wire-protocol-and-sandboxing.md#65_dual_tier_sandboxing): Fuel-bounded Wasm and rootless Podman containers.
  * [Hierarchical Work-Stealing](manual/ch65-edge-micro-mesh-binary-wire-protocol-and-sandboxing.md#65_hierarchical_work_stealing): Multi-tier work distribution across Local, LAN, and WAN.
* **[Chapter 66: V5 Database Authority Inversion, CephFS Storage Tiering & Disaster Recovery](manual/ch66-v5-authority-inversion-and-cephfs-tiering.md)**
  * [V5 Authority Inversion](manual/ch66-v5-authority-inversion-and-cephfs-tiering.md#66_v5_authority_inversion): PostgreSQL config_kv live authority and atomic materialization.
  * [CephFS Storage Tiering](manual/ch66-v5-authority-inversion-and-cephfs-tiering.md#66_cephfs_storage_tiering): Local NVMe fast tier and distributed CephFS subvolume quotas.
  * [Disaster Recovery](manual/ch66-v5-authority-inversion-and-cephfs-tiering.md#66_disaster_recovery): Pre-upgrade zstd backups and transactional database migrations.
* **[Chapter 67: Discrete GPU VFIO Passthrough, Looking Glass B6 IVSHMEM & Inter-VM Audio](manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md)**
  * [Dynamic VFIO Switching](manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md#67_dynamic_vfio_switching): PCIe IOMMU isolation and dynamic driver unbind/rebind.
  * [Looking Glass B6](manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md#67_looking_glass_b6): Shared memory framebuffers and SPICE direct socket input.
  * [PipeWire JACK Audio](manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md#67_pipewire_jack_audio): Sub-5ms Scream inter-VM audio bridge.
* **[Chapter 68: Living Wallpaper WebGL Shaders, FOSS Licensing & Real-Time Theme Synchronization](manual/ch68-living-wallpaper-shaders-and-ssot-theme-engine.md)**
  * [Living Wallpaper Shader](manual/ch68-living-wallpaper-shaders-and-ssot-theme-engine.md#68_living_wallpaper_shader): GPU load-adaptive WebGL/WGSL shaders and license filter.
  * [Live Theme Sync](manual/ch68-living-wallpaper-shaders-and-ssot-theme-engine.md#68_live_theme_sync): Multi-surface real-time IPC and signal theme propagation.
* **[Chapter 69: Autonomous Epistemic Evolution, /learn Distillation & Model Fine-Tuning](manual/ch69-autonomous-epistemic-evolution-and-learn-loop.md)**
  * [Four-Stage Epistemic Loop](manual/ch69-autonomous-epistemic-evolution-and-learn-loop.md#69_four_stage_epistemic_loop): Experience, distillation, dual persistence, and OCI image bake-in.
* **[Chapter 70: Upstream Technology Registry, Upstream Adoption Playbook & FOSS Drift Auditing](manual/ch70-upstream-technology-registry-and-ecosystem-advances.md)**
  * [Upstream Registry](manual/ch70-upstream-technology-registry-and-ecosystem-advances.md#70_upstream_registry): Comprehensive mapping of upstream components and roles.
  * [Drift Auditing](manual/ch70-upstream-technology-registry-and-ecosystem-advances.md#70_drift_auditing): Automated upstream drift detection and validation playbooks.

### Part VIII: Substrate Daemons, Resilient Clustering & Hardware Acceleration

* **[Chapter 71: Composefs fs-verity Root Filesystem Sealing](manual/ch71-composefs-sealing.md)**
  * [Composefs Architecture](manual/ch71-composefs-sealing.md#71_composefs_architecture): Details kernel-level composefs immutable root sealing architecture.
  * [Fs-Verity Integrity](manual/ch71-composefs-sealing.md#71_fsverity_integrity): Covers cryptographic Merkle tree verification of filesystem descriptors.
  * [Atomic Validator](manual/ch71-composefs-sealing.md#71_atomic_validator): Explains image descriptor validation tooling (mios-composefs-validator).
  * [Sealing Automation](manual/ch71-composefs-sealing.md#71_sealing_automation): Documents boot sealing automation and drop-in generation.
* **[Chapter 72: IDE Custom CSS and Edge-to-Edge Integrated Terminal](manual/ch72-ide-custom-css.md)**
  * [IDE Integration](manual/ch72-ide-custom-css.md): Covers provisioning vscode-custom-css and edge-to-edge terminal styling.
* **[Chapter 73: Global Per-User Encrypted CephFS Subvolumes](manual/ch73-cephfs-user-subvolumes.md)**
  * [Encrypted Subvolume Architecture](manual/ch73-cephfs-user-subvolumes.md): Covers mios-user-cephfs isolated subvolume provisioning.
* **[Chapter 74: Declarative SSOT Blade Pre-Enrollment Registry](manual/ch74-blade-pre-enrollment.md)**
  * [TPM EK Cryptography](manual/ch74-blade-pre-enrollment.md#74_tpm_ek_cryptography): Details TPM 2.0 Endorsement Key validation and fingerprinting.
  * [SSOT Schema](manual/ch74-blade-pre-enrollment.md#74_ssot_schema): Explains declarative [cluster.blades] admission records.
  * [Admission Workflow](manual/ch74-blade-pre-enrollment.md#74_admission_workflow): Documents zero-touch provisioning and onboarding sequence.
* **[Chapter 75: Peripheral Hardware Health Evaluator and Greenboot Degradation Reporter](manual/ch75-greenboot-hardware-degrade.md)**
  * [Degrade Not Refuse Philosophy](manual/ch75-greenboot-hardware-degrade.md#75_degrade_not_refuse_philosophy): Non-fatal hardware evaluation architecture.
  * [Peripheral Health Probes](manual/ch75-greenboot-hardware-degrade.md#75_peripheral_health_probes): Probing audio, video, NIC, and display states.
  * [Structured Telemetry](manual/ch75-greenboot-hardware-degrade.md#75_structured_telemetry_and_persistence): Hardware state persistence and alerts.
* **[Chapter 76: Automated RFC 9334 RATS Remote TPM 2.0 Quote Verifier](manual/ch76-remote-attestation.md)**
  * [RATS Architecture](manual/ch76-remote-attestation.md#76_rats_architecture): Details the RFC 9334 remote attestation pipeline.
  * [Attestation Lifecycle](manual/ch76-remote-attestation.md#76_attestation_lifecycle): Covers cryptographic nonces and golden PCR appraisal.
  * [Credential Issuance](manual/ch76-remote-attestation.md#76_credential_issuance): Explains zero-touch WireGuard credential issuance.
* **[Chapter 77: Automated Network and Audio Fallback Manager](manual/ch77-hardware-fallback.md)**
  * [Architecture Overview](manual/ch77-hardware-fallback.md#77_architecture_overview): Covers the resilient peripheral fallback daemon.
  * [Network Fallback](manual/ch77-hardware-fallback.md#77_network_fallback): Documents secondary NIC and USB tethering auto-failover.
  * [Audio Fallback](manual/ch77-hardware-fallback.md#77_audio_fallback): Explains dynamic PipeWire null-sink rebinding.
* **[Chapter 78: Low-Latency WebRTC Streaming Audio Ingress and Streaming Whisper Bridge](manual/ch78-streaming-audio-whisper.md)**
  * [WebRTC Ingress Protocol](manual/ch78-streaming-audio-whisper.md#78_webrtc_ingress_protocol): Details real-time WebRTC audio ingress framing.
  * [Streaming Whisper Engine](manual/ch78-streaming-audio-whisper.md#78_streaming_whisper_engine): Covers local whisper.cpp Quadlet streaming container.
  * [Token Dispatch and Latency Metrics](manual/ch78-streaming-audio-whisper.md#78_token_dispatch_and_latency_metrics): Sub-150ms VAD and token dispatch.
* **[Chapter 79: Concurrent Streaming Piper/Kokoro TTS Audio Synthesis and PipeWire Buffer Feeder](manual/ch79-streaming-tts-piper.md)**
  * [Streaming TTS Architecture](manual/ch79-streaming-tts-piper.md#79_streaming_tts_architecture): Covers sentence-chunked ONNX synthesis.
  * [PipeWire Buffer Feeder](manual/ch79-streaming-tts-piper.md#79_pipewire_buffer_feeder): Details low-latency PipeWire audio stream feeding.
  * [Quadlet Containerization](manual/ch79-streaming-tts-piper.md#79_quadlet_containerization): Explains mios-piper container execution.
* **[Chapter 80: Multi-Vendor Hardware Video Encoder Discovery and DMA-BUF Capture Bridge](manual/ch80-hardware-video-encoding.md)**
  * [Hardware Encoder Discovery](manual/ch80-hardware-video-encoding.md#80_hardware_encoder_discovery): QuickSync, NVENC, and AMF hardware probing.
  * [DMA-BUF Zero-Copy Capture](manual/ch80-hardware-video-encoding.md#80_dmabuf_zero_copy_architecture): Wayland zero-copy DMA-BUF frame capture.
  * [Codec Priority Hierarchy](manual/ch80-hardware-video-encoding.md#80_codec_priority_hierarchy): Documents AV1 > HEVC > AVC priority selection.
* **[Chapter 81: Adaptive Bitrate and Low-Latency Frame Encoding Streaming Benchmark Suite](manual/ch81-video-streaming-benchmarks.md)**
  * [Streaming Latency Budget](manual/ch81-video-streaming-benchmarks.md#81_streaming_latency_budget): Sub-12ms frame encoding SLA for 60 FPS.
  * [Adaptive Bitrate Control Loops](manual/ch81-video-streaming-benchmarks.md#81_adaptive_bitrate_control_loops): Real-time ABR modulation under congestion.
  * [Packet Loss Recovery](manual/ch81-video-streaming-benchmarks.md#81_packet_loss_recovery): Details intra-refresh IDR insertion upon packet loss.
* **[Chapter 82: Multi-Node Dynamic AI Workload Partitioning and Capability-Aware Task Routing](manual/ch82-mesh-workload-partitioning.md)**
  * [Mesh Topology Discovery](manual/ch82-mesh-workload-partitioning.md#82_mesh_topology_discovery): Cluster blade capability matrix scoring.
  * [Capability Routing Algorithm](manual/ch82-mesh-workload-partitioning.md#82_capability_routing_algorithm): Dynamic prompt routing based on node VRAM.
  * [Subtask Fan-out/Gather](manual/ch82-mesh-workload-partitioning.md#82_subtask_fanout_gather): Details subtask decomposition and fan-out/gather.
* **[Chapter 83: Automated Node Failure Detection and Zero-Loss Task Re-Distribution Engine](manual/ch83-task-failover-resilience.md)**
  * [Failure Detection Mechanics](manual/ch83-task-failover-resilience.md#83_failure_detection_mechanics): Node heartbeat monitoring and lease expiration.
  * [Task Leasing Protocols](manual/ch83-task-failover-resilience.md#83_task_leasing_protocol): Explains atomic task leasing across the cluster mesh.
  * [Zero-Loss Recovery](manual/ch83-task-failover-resilience.md#83_zero_loss_recovery): Documents dynamic re-routing of in-flight prompts.
* **[Chapter 84: ATSPI Sensitive Widget Detector and Wayland Frame Blur Filter](manual/ch84-vision-redaction.md)**
  * [Vision Redaction Overview](manual/ch84-vision-redaction.md#84_vision_redaction_overview): Covers on-device vision stream PII redaction.
  * [ATSPI Discovery](manual/ch84-vision-redaction.md#84_atspi_sensitive_widget_discovery): Explains accessibility tree password/secret widget queries.
  * [Wayland Capture Integration](manual/ch84-vision-redaction.md#84_wayland_capture_integration): Details real-time frame coordinate blur filtering.
* **[Chapter 85: Lightweight On-Device OCR Credential Masking Pipeline](manual/ch85-ocr-credential-masking.md)**
  * [Zero-Trust Vision Architecture](manual/ch85-ocr-credential-masking.md#85_zero_trust_vision_architecture): Local OCR credential sanitization.
  * [OCR Engine Integration](manual/ch85-ocr-credential-masking.md#85_ocr_engine_integration): Explains fast Tesseract OCR invocation on captured frames.
  * [Bounding Box Redaction](manual/ch85-ocr-credential-masking.md#85_bounding_box_redaction): Documents regex credential masking over vision boxes.
* **[Chapter 86: Shadow Dual-Process Candidate Daemon Validator and Query Mirroring Harness](manual/ch86-shadow-candidate-testing.md)**
  * [Shadow Mechanics](manual/ch86-shadow-candidate-testing.md#86_shadow_mechanics): Covers isolated execution of candidate daemon versions.
  * [Query Mirroring Architecture](manual/ch86-shadow-candidate-testing.md#86_query_mirroring): Details live request duplexing without mutating state.
  * [Divergence Scoring](manual/ch86-shadow-candidate-testing.md#86_divergence_scoring): Explains response comparison and regression scoring.
  * [Safe Promotion Gates](manual/ch86-shadow-candidate-testing.md#86_safe_promotion_gates): Documents zero-downtime candidate promotion criteria.
* **[Chapter 87: Zero-Downtime Systemd Socket Handoff Daemon Swapper for Agent-Pipe](manual/ch87-socket-activation-handoff.md)**
  * [Socket Activation Handoff](manual/ch87-socket-activation-handoff.md): Covers sd_listen_fds file descriptor passing and zero-downtime swaps.
* **[Chapter 88: Proactive PID Thermal Daemon and Dynamic CPU/GPU Power Cap Modulator](manual/ch88-proactive-thermal-governor.md)**
  * [Proactive Thermal Regulation](manual/ch88-proactive-thermal-governor.md#88_proactive_thermal_regulation_architecture): Sysfs thermal rate-of-rise tracking.
  * [PID Control and Rate-of-Rise](manual/ch88-proactive-thermal-governor.md#88_pid_control_and_rate_of_rise): Predictive throttling prior to hardware trip points.
  * [Dynamic Power Cap Actuation](manual/ch88-proactive-thermal-governor.md#88_dynamic_power_cap_actuation): Real-time modulation of CPU/GPU power limits.
* **[Chapter 89: Glass Desktop, Liquid Shell Controls, and Session Resilience](manual/ch89-glass-desktop-and-session-resilience.md)**
  * [Glass Window Effects](manual/ch89-glass-desktop-and-session-resilience.md#89_glass_window_effects): Hyprland windowshade shaders, the duration contract and fade gamma.
  * [Per-Window Glass Controls](manual/ch89-glass-desktop-and-session-resilience.md#89_per_window_glass_controls): Focus and proximity tabs, the glass pill and dynamic tab yielding.
  * [Top-Bar App-Menu Pills](manual/ch89-glass-desktop-and-session-resilience.md#89_topbar_appmenu_pills): miosd appmenu registrar and the Quickshell tab slide IPC.
  * [Float-First Window Placement](manual/ch89-glass-desktop-and-session-resilience.md#89_float_first_window_placement): Golden-ratio sizing and drag-to-tile.
  * [Fan Control Safety Model](manual/ch89-glass-desktop-and-session-resilience.md#89_fan_control_safety_model): ventulusd, curve clamping and firmware preservation.
  * [Looking Glass IDD Mirroring](manual/ch89-glass-desktop-and-session-resilience.md#89_looking_glass_idd_mirroring): Non-exclusive IDD and dual VNC/SPICE display mirroring.
  * [Traversal OOM Resilience](manual/ch89-glass-desktop-and-session-resilience.md#89_kernel_traversal_oom_resilience): The /proc/kcore traversal root cause and tmux scope OOMPolicy.
  * [Desktop Hardware Profiles](manual/ch89-glass-desktop-and-session-resilience.md#89_desktop_hardware_profiles): Shared Hyprland and Quickshell sessions, strict GPU selection, BLS hardware choices and signed UKI boundaries.

---

## The chapter files

Every chapter above is one authored file; this index is derived from
their AI-hint headers, so a missing or hint-less chapter turns the
`check_manual_generated` gate red.

<!-- MIOS-GEN:index:usr/share/doc/mios/manual/ch*.md -->
| File | What it is |
|---|---|
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
| `usr/share/doc/mios/manual/ch89-glass-desktop-and-session-resilience.md` | Chapter 89: Glass Desktop, Liquid Shell Controls, Hyper-Converged VM Display Mirroring, and Session Resilience. Covers Hyprland windowshade shaders, per-window glass controls and interior fallbacks,... |

<!-- derived from the AI-hint headers of 89 file(s) matching usr/share/doc/mios/manual/ch*.md -->
<!-- /MIOS-GEN:index:usr/share/doc/mios/manual/ch*.md -->
