#!/usr/bin/env python3
# AI-hint: Automated unit test suite for task_328 Dynamic Agent Persona Synthesis.
# AI-related: usr/lib/mios/agent-pipe/mios_persona.py, usr/lib/mios/agent-pipe/server.py
"""
Automated unit tests for domain intent classification, persona prompt specialization,
canonical law preservation, and agent-pipe integration.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_PERSONA_PATH = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe", "mios_persona.py")

spec = importlib.util.spec_from_file_location("mios_persona", _PERSONA_PATH)
if spec and spec.loader:
    mios_persona = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mios_persona
    spec.loader.exec_module(mios_persona)
else:
    raise ImportError(f"Could not load mios_persona module from {_PERSONA_PATH}")

class TestAgentPersona(unittest.TestCase):
    """Validates domain classification, persona synthesis, and canonical law preservation."""

    def setUp(self):
        self.classifier = mios_persona.PersonaClassifier()
        self.synthesizer = mios_persona.PersonaSynthesizer(self.classifier)
        self.base_system_prompt = (
            "You are a local MiOS system agent. Follow Architectural Laws 1-6. "
            "All endpoints resolve through MIOS_AI_ENDPOINT strictly."
        )

    def test_kernel_systems_classification(self):
        query = "How do I configure VFIO GPU whole-device passthrough and UKI kargs on bootc?"
        domain, confidence, scores = self.classifier.classify(query)
        self.assertEqual(domain, mios_persona.DomainCategory.KERNEL_SYSTEMS)
        self.assertGreaterEqual(confidence, 0.20)

    def test_database_storage_classification(self):
        query = "How to create an HNSW vector index in PostgreSQL with pgvector for 768-dim embeddings?"
        domain, confidence, scores = self.classifier.classify(query)
        self.assertEqual(domain, mios_persona.DomainCategory.DATABASE_STORAGE)
        self.assertGreaterEqual(confidence, 0.20)

    def test_security_crypto_classification(self):
        query = "Implement Ed25519 signature verification and ChaCha20-Poly1305 AEAD payload decryption."
        domain, confidence, scores = self.classifier.classify(query)
        self.assertEqual(domain, mios_persona.DomainCategory.SECURITY_CRYPTO)
        self.assertGreaterEqual(confidence, 0.20)

    def test_networking_mesh_classification(self):
        query = "Set up Tokio async TCP frame reader with 16-byte fixed headers and dead-peer heartbeat eviction."
        domain, confidence, scores = self.classifier.classify(query)
        self.assertEqual(domain, mios_persona.DomainCategory.NETWORKING_MESH)
        self.assertGreaterEqual(confidence, 0.20)

    def test_ai_inference_classification(self):
        query = "Configure llama.cpp behind llama-swap proxy for multi-model auto-swapping and MCP tool calls."
        domain, confidence, scores = self.classifier.classify(query)
        self.assertEqual(domain, mios_persona.DomainCategory.AI_INFERENCE)
        self.assertGreaterEqual(confidence, 0.20)

    def test_devops_ci_classification(self):
        query = "Validate mios.toml SSOT against drift-checks legibility ratchet and sync-generated projections."
        domain, confidence, scores = self.classifier.classify(query)
        self.assertEqual(domain, mios_persona.DomainCategory.DEVOPS_CI)
        self.assertGreaterEqual(confidence, 0.20)

    def test_generalist_fallback_on_generic_prompt(self):
        query = "Hello, tell me a friendly story about computers."
        domain, confidence, _ = self.classifier.classify(query)
        self.assertEqual(domain, mios_persona.DomainCategory.GENERALIST)

    def test_persona_synthesis_preserves_base_prompt_and_laws(self):
        augmented = self.synthesizer.synthesize(
            self.base_system_prompt,
            mios_persona.DomainCategory.KERNEL_SYSTEMS,
            confidence=0.85,
        )
        # Verify base prompt and invariants are retained verbatim
        self.assertIn("You are a local MiOS system agent", augmented)
        self.assertIn("MIOS_AI_ENDPOINT", augmented)
        self.assertIn("Architectural Laws 1-6", augmented)

        # Verify specialization guidelines are injected
        self.assertIn("ACTIVE DOMAIN SPECIALIZATION: Linux Kernel & Systems Engineering Specialist", augmented)
        self.assertIn("Architectural Law 1 (USR-OVER-ETC)", augmented)
        self.assertIn("Unified Kernel Image (UKI)", augmented)

    def test_convenience_module_functions(self):
        domain, conf = mios_persona.classify_intent("Postgres pgvector table migration")
        self.assertEqual(domain, mios_persona.DomainCategory.DATABASE_STORAGE)

        prompt = mios_persona.synthesize_persona_prompt(
            self.base_system_prompt,
            "Ed25519 authentication and LUKS TPM2 clevis unlock",
        )
        self.assertIn("Cryptographic Security & System Hardening Auditor", prompt)

        guidelines = mios_persona.get_domain_guidelines(mios_persona.DomainCategory.NETWORKING_MESH)
        self.assertIn("16-Byte Binary Framing", guidelines)

def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAgentPersona)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
