#!/usr/bin/env python3
# AI-hint: Dynamic agent persona synthesis based on task domain classification.
# AI-related: usr/lib/mios/agent-pipe/server.py, usr/share/mios/ai/system.md
"""
Dynamic Agent Persona Synthesis (task_328 / task_2561)

Classifies user query intent across 6 specialized technical domains (Kernel/Systems,
Database/Storage, Security/Crypto, Networking/Mesh, AI/Inference, DevOps/CI) and
synthesizes enriched system prompts with domain-specific technical rigor and guidelines
while strictly preserving canonical project laws and OpenAI endpoint contracts.
"""

from __future__ import annotations

from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mios.persona")

class DomainCategory(str, Enum):
    KERNEL_SYSTEMS = "kernel_systems"
    DATABASE_STORAGE = "database_storage"
    SECURITY_CRYPTO = "security_crypto"
    NETWORKING_MESH = "networking_mesh"
    AI_INFERENCE = "ai_inference"
    DEVOPS_CI = "devops_ci"
    GENERALIST = "generalist"

DOMAIN_TITLES: Dict[DomainCategory, str] = {
    DomainCategory.KERNEL_SYSTEMS: "Linux Kernel & Systems Engineering Specialist",
    DomainCategory.DATABASE_STORAGE: "High-Throughput Database & Vector Storage Architect",
    DomainCategory.SECURITY_CRYPTO: "Cryptographic Security & System Hardening Auditor",
    DomainCategory.NETWORKING_MESH: "Distributed Tokio Mesh & Wire Protocol Engineer",
    DomainCategory.AI_INFERENCE: "Local LLM Inference & Agent Orchestration Specialist",
    DomainCategory.DEVOPS_CI: "DevOps, Containerfile & SSOT Compliance Engineer",
    DomainCategory.GENERALIST: "Canonical MiOS Systems Generalist",
}

DOMAIN_KEYWORDS: Dict[DomainCategory, List[str]] = {
    DomainCategory.KERNEL_SYSTEMS: [
        "kernel", "bootc", "ostree", "fhs", "uki", "kargs", "vfio", "iommu",
        "cgroups", "systemd", "systemctl", "journald", "dracut", "grub", "virtio",
        "kvm", "qemu", "libvirt", "passthrough", "mdevctl", "initramfs", "rootfs",
        "sysfs", "procfs", "device-tree", "module", "sysctl", "bpf", "ebpf",
    ],
    DomainCategory.DATABASE_STORAGE: [
        "postgres", "postgresql", "pgvector", "ceph", "cephfs", "sqlite", "wal",
        "vector", "embedding", "hnsw", "ivfflat", "cosine", "sql", "database",
        "table", "migration", "query", "acid", "storage", "nvme", "btrfs", "zfs",
        "disk", "partition", "volume", "schema", "index",
    ],
    DomainCategory.SECURITY_CRYPTO: [
        "crypto", "ed25519", "x25519", "chacha20", "poly1305", "chacha20-poly1305", "aead",
        "tpm", "tpm2", "luks", "clevis", "seccomp", "selinux", "sandbox", "attestation", "pki",
        "certificate", "tls", "mtls", "secret", "redact", "crl", "auth", "rbac",
        "signature", "verify", "verification", "hash", "hmac", "encryption", "decryption", "firewall", "hardened",
    ],
    DomainCategory.NETWORKING_MESH: [
        "tokio", "mesh", "wire", "framing", "header", "crc32", "crdt", "gossip",
        "mdns", "heartbeat", "dead-peer", "eviction", "tcp", "udp", "socket",
        "port", "bandwidth", "latency", "routing", "node", "packet", "stream",
        "codec", "peer", "handshake", "broadcast", "multicast",
    ],
    DomainCategory.AI_INFERENCE: [
        "llm", "llama", "llama.cpp", "llama-swap", "vllm", "sglang", "gguf",
        "kv-cache", "context", "token", "prompt", "sampling", "temperature",
        "mcp", "tool_call", "function_call", "openai", "embeddings", "nomic",
        "inference", "agent-pipe", "hermes", "reasoning", "deliberation",
    ],
    DomainCategory.DEVOPS_CI: [
        "ci", "toml", "ssot", "ratchet", "drift", "lint", "containerfile",
        "podman", "quadlet", "pipeline", "build", "package", "rpm", "flatpak",
        "git", "forgejo", "workflow", "legibility", "sync-generated", "roadmap",
    ],
}

DOMAIN_GUIDELINES: Dict[DomainCategory, str] = {
    DomainCategory.KERNEL_SYSTEMS: """
[SPECIALIZED ROLE: LINUX KERNEL & SYSTEMS ENGINEERING SPECIALIST]
1. Architectural Law 1 (USR-OVER-ETC): `/usr` is strictly immutable under bootc. All configuration changes must target `/etc` overrides and runtime state must target `/var`.
2. Unified Kernel Image (UKI) Invariant: Kernel command line arguments (kargs) are signed into the UKI (`shim -> systemd-boot -> signed UKI`); never conflate with MOK module signing.
3. Graphics Virtualization & VFIO: The `venus` VirtIO protocol is graphics/Vulkan only (no CUDA inside microVMs). CUDA requires whole-device VFIO hardware passthrough (`vfio-pci`).
4. Resource Isolation: Utilize Linux cgroups v2 (`cpu.max`, `memory.max`) and avoid pinning real-time tasks to CPU 0 (system reserved).
""".strip(),

    DomainCategory.DATABASE_STORAGE: """
[SPECIALIZED ROLE: HIGH-THROUGHPUT DATABASE & VECTOR STORAGE ARCHITECT]
1. Unified Agent Datastore: PostgreSQL + pgvector (`:5432`) is the authoritative system-wide persistence layer for episodic memory, vector recall, and skill embeddings.
2. Vector Indexing: Utilize HNSW (`m=16`, `ef_construction=64`) or IVFFlat with cosine distance `<=>` for embeddings (e.g. `nomic-embed-text` 768-dim vectors).
3. ACID & Durability: Guarantee write-ahead logging (WAL) and transactional consistency. Never store state in volatile tmpfs when `/var` persists across boots.
4. CephFS Distributed Storage: Ensure POSIX compliance and multi-attach volume semantics across node cluster storage backends.
""".strip(),

    DomainCategory.SECURITY_CRYPTO: """
[SPECIALIZED ROLE: CRYPTOGRAPHIC SECURITY & SYSTEM HARDENING AUDITOR]
1. Mutual Cryptographic Authentication: Enforce Ed25519 identity verification and X25519 Diffie-Hellman ephemeral key exchange for inter-node communication.
2. AEAD Wire Encryption: Encrypt frame payloads using ChaCha20-Poly1305 with HKDF-SHA256 derived keys and monotonic nonce sequencing.
3. Zero-Secret Retention (Rule 14): Redact API keys (`sk-*`), passwords, private keys, and session tokens before persisting logs or datasets.
4. Auto-Unlock & Sandboxing: Rely on TPM2 PCR binding with Clevis/LUKS for automated volume unlocking, and restrict unprivileged processes via seccomp filters.
""".strip(),

    DomainCategory.NETWORKING_MESH: """
[SPECIALIZED ROLE: DISTRIBUTED TOKIO MESH & WIRE PROTOCOL ENGINEER]
1. 16-Byte Binary Framing: Enforce fixed wire header: `0x4D49` (2B Magic), `Version` (1B), `Opcode` (1B), `NodeID` (4B), `PayloadLen` (4B), `CRC32` (4B).
2. Asynchronous Tokio Actors: Handle all network I/O via non-blocking async codecs and bounded `tokio::sync::mpsc` channels to avoid thread pool exhaustion.
3. Heartbeat & Dead-Peer Eviction: Broadcast heartbeats every 5s; mark nodes degraded at 2 missed beats (10s) and evict from cluster routing tables at 3 misses (15s).
4. CRDT State Synchronization: Employ state-based conflict-free replicated data types with vector clocks for decentralized node state convergence.
""".strip(),

    DomainCategory.AI_INFERENCE: """
[SPECIALIZED ROLE: LOCAL LLM INFERENCE & AGENT ORCHESTRATION SPECIALIST]
1. Architectural Law 5 (UNIFIED-AI-REDIRECTS): All AI consumers communicate strictly over `MIOS_AI_ENDPOINT` (`/v1/chat/completions`, `/v1/embeddings`) with zero cloud-vendor fallback.
2. Primary Inference Lane: `mios-llm-light` (llama.cpp with `llama-swap` proxy configured via `[ports].llm_light`) provides multi-model auto-swapping, KV-cache paging, and embedding generation.
3. Function Calling & MCP: Tool invocation loops adhere to the OpenAI function-calling standard over the unified MCP surface.
4. VRAM Budgeting: Heavy GPU lanes (`vllm`, `sglang`) remain gated and off-by-default to prevent host memory starvation.
""".strip(),

    DomainCategory.DEVOPS_CI: """
[SPECIALIZED ROLE: DEVOPS, CONTAINERFILE & SSOT COMPLIANCE ENGINEER]
1. SSOT Principle: `mios.toml` is the singular source of truth for packages, services, ports, and configuration. Never hardcode tunable parameters.
2. Legibility Ratchet Floors: Maintain strict ratchet invariants (`max_libexec_verbs = 285/285`, `ps_lines = 22618/22618`).
3. Modular Libexec Structure: Place new tools in modular domain directories (`usr/libexec/mios/<domain>/`) to avoid violating root verb limits.
4. Deterministic CI Verification: Verify all 7 validation gates pass with exit code 0 before finalizing changes.
""".strip(),

    DomainCategory.GENERALIST: """
[SPECIALIZED ROLE: CANONICAL MIOS SYSTEMS GENERALIST]
1. Maintain strict adherence to all Six Architectural Laws and Native Linux FHS structuration.
2. Produce verifiable, self-contained, reproducible solutions backed by automated tests.
3. Follow the minimal change principle with genuine, production-grade logic.
""".strip(),
}

class PersonaClassifier:
    """Classifies user prompts into one of the 6 technical domain categories."""

    def __init__(self, confidence_threshold: float = 0.15) -> None:
        self.confidence_threshold = confidence_threshold

    def classify(self, text: str) -> Tuple[DomainCategory, float, Dict[DomainCategory, float]]:
        if not text or not text.strip():
            return DomainCategory.GENERALIST, 1.0, {DomainCategory.GENERALIST: 1.0}

        text_lower = text.lower()
        words = re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", text_lower)
        subwords = re.findall(r"[a-zA-Z0-9]{2,}", text_lower)
        word_set = set(words).union(set(subwords))

        scores: Dict[DomainCategory, float] = {}

        for domain, keywords in DOMAIN_KEYWORDS.items():
            matches = 0
            for kw in keywords:
                if kw in word_set or f" {kw} " in text_lower or text_lower.startswith(f"{kw} ") or text_lower.endswith(f" {kw}") or (len(kw) >= 5 and kw in text_lower):
                    matches += 1
            # Weighted score based on keyword match density
            score = (matches * 2.5) / max(len(keywords), 5)
            scores[domain] = min(round(score, 4), 1.0)

        # Find best domain
        best_domain = DomainCategory.GENERALIST
        best_score = 0.0

        for domain, score in scores.items():
            if score > best_score:
                best_score = score
                best_domain = domain

        if best_score < self.confidence_threshold:
            return DomainCategory.GENERALIST, 0.5, scores

        return best_domain, best_score, scores

class PersonaSynthesizer:
    """Synthesizes domain-enriched prompts while preserving base system prompt."""

    def __init__(self, classifier: Optional[PersonaClassifier] = None) -> None:
        self.classifier = classifier or PersonaClassifier()

    def synthesize(
        self,
        base_prompt: str,
        domain: DomainCategory,
        confidence: float = 1.0,
    ) -> str:
        guideline = DOMAIN_GUIDELINES.get(domain, DOMAIN_GUIDELINES[DomainCategory.GENERALIST])
        title = DOMAIN_TITLES.get(domain, DOMAIN_TITLES[DomainCategory.GENERALIST])

        specialization_block = (
            f"\n\n---\n"
            f"### ACTIVE DOMAIN SPECIALIZATION: {title}\n"
            f"**Classification Confidence:** {confidence:.1%}\n\n"
            f"{guideline}\n"
            f"---\n"
        )

        return base_prompt.rstrip() + specialization_block

    def process_prompt(self, base_prompt: str, user_query: str) -> Tuple[str, DomainCategory, float]:
        domain, conf, _ = self.classifier.classify(user_query)
        if domain == DomainCategory.GENERALIST and conf < 0.2:
            return base_prompt, domain, conf
        augmented = self.synthesize(base_prompt, domain, confidence=conf)
        return augmented, domain, conf

# Convenience module-level instances
_DEFAULT_CLASSIFIER = PersonaClassifier()
_DEFAULT_SYNTHESIZER = PersonaSynthesizer(_DEFAULT_CLASSIFIER)

def classify_intent(query: str) -> Tuple[DomainCategory, float]:
    domain, conf, _ = _DEFAULT_CLASSIFIER.classify(query)
    return domain, conf

def synthesize_persona_prompt(base_prompt: str, user_query: str) -> str:
    augmented, _, _ = _DEFAULT_SYNTHESIZER.process_prompt(base_prompt, user_query)
    return augmented

def get_domain_guidelines(domain: DomainCategory) -> str:
    return DOMAIN_GUIDELINES.get(domain, DOMAIN_GUIDELINES[DomainCategory.GENERALIST])
