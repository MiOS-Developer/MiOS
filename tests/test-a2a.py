#!/usr/bin/env python3
# AI-hint: Consolidated unit test suite for MiOS A2A (Agent-to-Agent) federation: Ed25519 cryptographic attestation, mutual capability handshake, and identity-aware delegation (task_3353 / GATECAT-01).
# AI-related: usr/libexec/mios/a2a/attestation.py, usr/lib/mios/agent-pipe/mios_a2a_delegation.py
"""Consolidated A2A Domain Test Suite.

Consolidates:
- Mutual capability attestation and Ed25519 signing (test-a2a-attestation.py)
- Identity-aware A2A delegation and frame negotiation (test-a2a-delegation.py)
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

from cryptography.hazmat.primitives import serialization

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_AGENT_PIPE = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe")
if _AGENT_PIPE not in sys.path:
    sys.path.insert(0, _AGENT_PIPE)
_A2A_DIR = os.path.join(_ROOT, "usr", "libexec", "mios", "a2a")
if _A2A_DIR not in sys.path:
    sys.path.insert(0, _A2A_DIR)

_ATTESTATION_PATH = os.path.join(_A2A_DIR, "attestation.py")
spec = importlib.util.spec_from_file_location("attestation", _ATTESTATION_PATH)
if spec and spec.loader:
    attestation = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = attestation
    spec.loader.exec_module(attestation)
else:
    raise ImportError(f"Could not load attestation module from {_ATTESTATION_PATH}")

from mios_a2a_delegation import (
    AgentCard as DelegationAgentCard,
    DelegationRouter,
    PayloadMode,
    DelegationFrame,
)


# ============================================================================
# Domain 1.1: Cryptographic Attestation & Mutual Capability Handshake
# (Migrated from tests/test-a2a-attestation.py)
# ============================================================================

class TestA2AAttestation(unittest.TestCase):
    """Validates Ed25519 key management, AgentCard signing, tampering rejection, clock skew, and negotiation."""

    def setUp(self):
        self.auth = attestation.A2AAuthenticator.generate_keypair(node_id=101)
        self.peer_auth = attestation.A2AAuthenticator.generate_keypair(node_id=102)

    def test_keypair_generation_and_export(self):
        self.assertEqual(self.auth.node_id, 101)
        self.assertIsNotNone(self.auth.private_key)
        self.assertIsNotNone(self.auth.public_key)
        self.assertEqual(len(self.auth.public_key_bytes), 32)
        self.assertEqual(len(self.auth.public_key_hex), 64)
        self.assertEqual(len(self.auth.private_key_bytes), 32)
        self.assertEqual(len(self.auth.private_key_hex), 64)

        # Restore from hex
        restored_priv = attestation.A2AAuthenticator.from_private_key(self.auth.private_key_hex, node_id=101)
        self.assertEqual(restored_priv.public_key_hex, self.auth.public_key_hex)
        self.assertEqual(restored_priv.private_key_hex, self.auth.private_key_hex)

        # Restore verify-only from public key hex
        restored_pub = attestation.A2AAuthenticator.from_public_key(self.auth.public_key_hex, node_id=101)
        self.assertIsNone(restored_pub.private_key)
        self.assertEqual(restored_pub.public_key_hex, self.auth.public_key_hex)

        # Restore from PEM bytes
        pem_priv = self.auth.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        auth_from_pem = attestation.A2AAuthenticator.from_private_key(pem_priv, node_id=101)
        self.assertEqual(auth_from_pem.public_key_hex, self.auth.public_key_hex)

        pem_pub = self.auth.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        auth_from_pem_pub = attestation.A2AAuthenticator.from_public_key(pem_pub, node_id=101)
        self.assertEqual(auth_from_pem_pub.public_key_hex, self.auth.public_key_hex)

        # Attempting create_card without private key should raise ValueError
        with self.assertRaises(ValueError):
            restored_pub.create_card(agent_name="verify_only", capabilities=["test"])

    def test_invalid_key_formats_raise(self):
        with self.assertRaises(ValueError):
            attestation._load_pub_key("invalid_short_hex")
        with self.assertRaises(ValueError):
            attestation._load_priv_key("invalid_short_hex")

    def test_agent_card_creation_and_fields(self):
        card = self.auth.create_card(
            agent_name="executor_node",
            capabilities=["sql_query", "code_exec", "file_io"],
            endpoints={"rpc": "http://127.0.0.1:8640/a2a", "ws": "ws://127.0.0.1:8640/a2a/ws"},
            ttl_seconds=1800,
            node_id=101,
        )
        self.assertEqual(card["agent_name"], "executor_node")
        self.assertEqual(card["node_id"], 101)
        # Capabilities should be sorted
        self.assertEqual(card["capabilities"], ["code_exec", "file_io", "sql_query"])
        self.assertEqual(card["endpoints"]["rpc"], "http://127.0.0.1:8640/a2a")
        self.assertEqual(card["public_key"], self.auth.public_key_hex)
        self.assertIn("nonce", card)
        self.assertIn("sig", card)
        self.assertEqual(len(card["sig"]), 128)  # 64 bytes in hex
        self.assertGreater(card["expires_at"], card["issued_at"])
        self.assertEqual(card["expires_at"] - card["issued_at"], 1800)

    def test_signature_verification_success(self):
        card = self.auth.create_card(
            agent_name="executor_node",
            capabilities=["code_exec", "sql_query"],
            ttl_seconds=3600,
        )
        # Verify via static method with trusted key object
        self.assertTrue(attestation.A2AAuthenticator.verify_card(card, self.auth.public_key))
        # Verify via static method with trusted key hex string
        self.assertTrue(attestation.A2AAuthenticator.verify_card(card, self.auth.public_key_hex))
        # Verify via static method with trusted key bytes
        self.assertTrue(attestation.A2AAuthenticator.verify_card(card, self.auth.public_key_bytes))
        # Verify via module-level convenience function
        self.assertTrue(attestation.verify_card(card, self.auth.public_key_hex))
        # Verify with self-embedded public key when trusted_key is omitted
        self.assertTrue(attestation.A2AAuthenticator.verify_card(card))

    def test_wrong_trusted_key_rejected(self):
        card = self.auth.create_card(
            agent_name="executor_node",
            capabilities=["code_exec"],
            ttl_seconds=3600,
        )
        # Verifying with peer's public key must fail
        self.assertFalse(attestation.A2AAuthenticator.verify_card(card, self.peer_auth.public_key))
        self.assertFalse(attestation.A2AAuthenticator.verify_card(card, self.peer_auth.public_key_hex))

    def test_tampered_payload_rejected(self):
        card = self.auth.create_card(
            agent_name="executor_node",
            capabilities=["code_exec"],
            ttl_seconds=3600,
        )
        # Tamper capabilities
        t_caps = dict(card)
        t_caps["capabilities"] = ["code_exec", "admin_escalation"]
        self.assertFalse(attestation.A2AAuthenticator.verify_card(t_caps, self.auth.public_key))

        # Tamper agent_name
        t_name = dict(card)
        t_name["agent_name"] = "imposter_node"
        self.assertFalse(attestation.A2AAuthenticator.verify_card(t_name, self.auth.public_key))

        # Tamper nonce
        t_nonce = dict(card)
        t_nonce["nonce"] = "00000000000000000000000000000000"
        self.assertFalse(attestation.A2AAuthenticator.verify_card(t_nonce, self.auth.public_key))

        # Tamper timestamps
        t_time = dict(card)
        t_time["issued_at"] = card["issued_at"] - 100
        self.assertFalse(attestation.A2AAuthenticator.verify_card(t_time, self.auth.public_key))

        # Tamper public_key in payload
        t_pub = dict(card)
        t_pub["public_key"] = self.peer_auth.public_key_hex
        self.assertFalse(attestation.A2AAuthenticator.verify_card(t_pub, self.auth.public_key))

        # Tamper signature bytes
        t_sig = dict(card)
        t_sig["sig"] = "00" * 64
        self.assertFalse(attestation.A2AAuthenticator.verify_card(t_sig, self.auth.public_key))

    def test_clock_skew_and_expiration(self):
        now = time.time()

        # Expired card (expired 120 seconds ago, max clock skew 60s)
        expired_card = self.auth.create_card(
            agent_name="node",
            capabilities=["ping"],
            issued_at=int(now - 1000),
            ttl_seconds=800,  # expired 200s ago
        )
        self.assertFalse(attestation.A2AAuthenticator.verify_card(expired_card, self.auth.public_key, max_clock_skew=60))

        # Card slightly expired within clock skew window
        borderline_expired = self.auth.create_card(
            agent_name="node",
            capabilities=["ping"],
            issued_at=int(now - 300),
            ttl_seconds=280,  # expired 20s ago
        )
        # Should be valid when max_clock_skew is 60s
        self.assertTrue(attestation.A2AAuthenticator.verify_card(borderline_expired, self.auth.public_key, max_clock_skew=60))
        # Should fail if clock skew is strictly 0
        self.assertFalse(attestation.A2AAuthenticator.verify_card(borderline_expired, self.auth.public_key, max_clock_skew=0))

        # Future timestamp beyond clock skew
        future_card = self.auth.create_card(
            agent_name="node",
            capabilities=["ping"],
            issued_at=int(now + 200),
            ttl_seconds=3600,
        )
        self.assertFalse(attestation.A2AAuthenticator.verify_card(future_card, self.auth.public_key, max_clock_skew=60))

        # Future timestamp within clock skew window
        borderline_future = self.auth.create_card(
            agent_name="node",
            capabilities=["ping"],
            issued_at=int(now + 30),
            ttl_seconds=3600,
        )
        self.assertTrue(attestation.A2AAuthenticator.verify_card(borderline_future, self.auth.public_key, max_clock_skew=60))

        # Invalid TTL (expires_at <= issued_at)
        invalid_ttl_card = self.auth.create_card(
            agent_name="node",
            capabilities=["ping"],
            issued_at=int(now),
            ttl_seconds=0,
        )
        self.assertFalse(attestation.A2AAuthenticator.verify_card(invalid_ttl_card, self.auth.public_key))

    def test_malformed_card_inputs(self):
        # Non-dict input
        self.assertFalse(attestation.A2AAuthenticator.verify_card("not a dict", self.auth.public_key))
        self.assertFalse(attestation.A2AAuthenticator.verify_card(None, self.auth.public_key))

        # Missing signature
        self.assertFalse(attestation.A2AAuthenticator.verify_card({"agent_name": "test"}, self.auth.public_key))

        # Invalid signature hex length
        self.assertFalse(attestation.A2AAuthenticator.verify_card({
            "agent_name": "test",
            "issued_at": int(time.time()),
            "expires_at": int(time.time() + 3600),
            "sig": "abcdef",
        }, self.auth.public_key))

        # Missing timestamps
        self.assertFalse(attestation.A2AAuthenticator.verify_card({
            "agent_name": "test",
            "sig": "00" * 64,
        }, self.auth.public_key))

    def test_capability_negotiation(self):
        card = self.auth.create_card(
            agent_name="worker_mesh",
            capabilities=["code_exec", "file_io", "sql_query", "wasm_eval"],
            ttl_seconds=3600,
        )

        # Full match
        ok, granted = attestation.A2AAuthenticator.negotiate_capabilities(
            card,
            required_capabilities=["code_exec", "sql_query"],
            trusted_key=self.auth.public_key,
        )
        self.assertTrue(ok)
        self.assertEqual(granted, ["code_exec", "sql_query"])

        # Empty required capabilities -> full pass
        ok, granted = attestation.A2AAuthenticator.negotiate_capabilities(
            card,
            required_capabilities=[],
            trusted_key=self.auth.public_key,
        )
        self.assertTrue(ok)
        self.assertEqual(granted, [])

        # Partial match / missing capability
        ok, missing = attestation.A2AAuthenticator.negotiate_capabilities(
            card,
            required_capabilities=["code_exec", "root_shell_exec"],
            trusted_key=self.auth.public_key,
        )
        self.assertFalse(ok)
        self.assertEqual(missing, ["root_shell_exec"])

        # Tampered card during negotiation
        tampered = dict(card)
        tampered["capabilities"] = ["code_exec", "root_shell_exec"]
        ok, caps = attestation.A2AAuthenticator.negotiate_capabilities(
            tampered,
            required_capabilities=["code_exec"],
            trusted_key=self.auth.public_key,
        )
        self.assertFalse(ok)
        self.assertEqual(caps, [])

        # Module-level convenience function
        ok, granted = attestation.negotiate_capabilities(
            card,
            required_capabilities=["file_io"],
            trusted_key=self.auth.public_key,
        )
        self.assertTrue(ok)
        self.assertEqual(granted, ["file_io"])

    def test_cli_interface(self):
        with tempfile.TemporaryDirectory(prefix="mios-a2a-cli-test-") as tmpdir:
            priv_file = os.path.join(tmpdir, "node.priv")
            pub_file = os.path.join(tmpdir, "node.pub")
            card_file = os.path.join(tmpdir, "card.json")

            # 0. Test CLI with no action prints help
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main([])
            self.assertEqual(code, 1)

            # 1. Test CLI keygen
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main(["keygen", "--node-id", "201", "--out-priv", priv_file, "--out-pub", pub_file])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(priv_file))
            self.assertTrue(os.path.isfile(pub_file))

            # 2. Test CLI sign-card
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main([
                    "sign-card",
                    "--agent", "cli_agent",
                    "--capabilities", "code_exec,sql_query",
                    "--key", priv_file,
                    "--node-id", "201",
                    "--ttl", "1800",
                    "--out", card_file,
                ])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(card_file))

            with open(card_file, "r", encoding="utf-8") as f:
                card_data = json.load(f)
            self.assertEqual(card_data["agent_name"], "cli_agent")

            # 3. Test CLI verify-card (Valid)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main(["verify-card", "--card", card_file, "--key", pub_file])
            self.assertEqual(code, 0)
            self.assertIn('"valid": true', stdout.getvalue())

            # Test CLI verify-card with invalid JSON
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main(["verify-card", "--card", "not_valid_json"])
            self.assertEqual(code, 1)
            self.assertIn('"valid": false', stdout.getvalue())

            # 4. Test CLI negotiate (Success)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main([
                    "negotiate",
                    "--card", card_file,
                    "--required", "code_exec",
                    "--key", pub_file,
                ])
            self.assertEqual(code, 0)
            self.assertIn('"authenticated": true', stdout.getvalue())

            # 5. Test CLI negotiate (Missing capability)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main([
                    "negotiate",
                    "--card", card_file,
                    "--required", "root_admin",
                    "--key", pub_file,
                ])
            self.assertEqual(code, 1)
            self.assertIn('"authenticated": false', stdout.getvalue())

            # Test CLI negotiate with invalid JSON
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = attestation.main([
                    "negotiate",
                    "--card", "bad_json",
                    "--required", "code_exec",
                ])
            self.assertEqual(code, 1)
            self.assertIn('"authenticated": false', stdout.getvalue())


# ============================================================================
# Domain 1.2: Identity-Aware Progressive Payload Negotiation & Delegation
# (Migrated from tests/test-a2a-delegation.py)
# ============================================================================

def _make_router() -> DelegationRouter:
    router = DelegationRouter()
    router.register(DelegationAgentCard(
        agent_id="agent-full",
        endpoint="http://localhost:8640",
        supported_interfaces=["text", "semantic_frame", "embedding_hints"],
        reasoning_profile="deliberate",
        cost_hint=0.8,
        capabilities={"summarize": True, "code": True},
    ))
    router.register(DelegationAgentCard(
        agent_id="agent-text-only",
        endpoint="http://localhost:8641",
        supported_interfaces=["text"],
        reasoning_profile="fast",
        cost_hint=0.3,
        capabilities={"summarize": True},
    ))
    return router


class TestA2ADelegation(unittest.TestCase):
    """Validates A2A payload mode negotiation, progressive frames, peer selection, and wire serialization."""

    def test_negotiate_semantic_frame_when_both_support(self):
        """Negotiation selects semantic_frame when both peers support it."""
        router = _make_router()
        src = router.get_card("agent-full")
        tgt = router.get_card("agent-full")
        mode = router.negotiate_mode(src, tgt)
        self.assertEqual(mode, PayloadMode.EMBEDDING_HINTS)

    def test_negotiate_fallback_to_text(self):
        """Negotiation falls back to text when one peer only supports text."""
        router = _make_router()
        src = router.get_card("agent-full")
        tgt = router.get_card("agent-text-only")
        mode = router.negotiate_mode(src, tgt)
        self.assertEqual(mode, PayloadMode.TEXT)

    def test_build_frame_uses_negotiated_mode(self):
        """build_frame() packs structured content for capable peers."""
        router = _make_router()
        frame = router.build_frame(
            "agent-full", "agent-full",
            content_text="plain fallback",
            semantic_frame={"intent": "summarize", "key": "v"},
            embedding_hints=[0.1, 0.2, 0.3],
        )
        self.assertIn(frame.mode, (PayloadMode.SEMANTIC_FRAME, PayloadMode.EMBEDDING_HINTS))

    def test_best_peer_by_capability(self):
        """best_peer() returns the lowest cost_hint peer with that capability."""
        router = _make_router()
        peer = router.best_peer("summarize")
        self.assertIsNotNone(peer)
        self.assertEqual(peer.agent_id, "agent-text-only")  # cost_hint=0.3 < 0.8

    def test_wire_roundtrip(self):
        """DelegationFrame serializes and deserializes cleanly."""
        frame = DelegationFrame(
            mode=PayloadMode.SEMANTIC_FRAME,
            content={"intent": "test"},
            source_agent="a", target_agent="b",
        )
        d = frame.to_wire()
        frame2 = DelegationFrame.from_wire(d)
        self.assertEqual(frame2.mode, PayloadMode.SEMANTIC_FRAME)
        self.assertEqual(frame2.content["intent"], "test")

    def test_recursive_task_delegation_chain(self):
        """Validates multi-hop recursive task delegation from Agent A to Agent B to Agent C."""
        router = _make_router()
        router.register(DelegationAgentCard(
            agent_id="agent-eval",
            endpoint="http://localhost:8642",
            supported_interfaces=["text", "semantic_frame"],
            reasoning_profile="deliberate",
            cost_hint=0.5,
            capabilities={"eval": True},
        ))
        # Hop 1: A -> B
        frame_hop1 = router.build_frame("agent-full", "agent-text-only", content_text="initial task")
        self.assertEqual(frame_hop1.mode, PayloadMode.TEXT)
        self.assertEqual(frame_hop1.source_agent, "agent-full")
        self.assertEqual(frame_hop1.target_agent, "agent-text-only")

        # Hop 2: B -> C (recursive subtask delegation)
        frame_hop2 = router.build_frame(
            frame_hop1.target_agent, "agent-eval",
            content_text="subtask",
            semantic_frame={"subtask": "eval_result"},
        )
        self.assertEqual(frame_hop2.source_agent, "agent-text-only")
        self.assertEqual(frame_hop2.target_agent, "agent-eval")
        self.assertEqual(frame_hop2.mode, PayloadMode.TEXT)  # agent-text-only only supports text

# ============================================================================
# Domain 1.3: ADR-0029 Standards Mapping, Receipt Invariant, Credential Refusal,
#             and Signed AgentCard Projection (task_198 / assign_002)
# ============================================================================

_LIB_MIOS = os.path.join(_ROOT, "usr", "lib", "mios")
if _LIB_MIOS not in sys.path:
    sys.path.insert(0, _LIB_MIOS)

import mios_translate
from mios_pipe.federation.agentcard_sign import (
    _verify_agent_card_signature,
    _b64u,
    _b64u_decode,
    _jcs_canonicalize,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey, Ed25519PrivateKey
from pathlib import Path
from importlib.machinery import SourceFileLoader
import subprocess

_SERVER_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-mcp-server")
try:
    _mcp_server_mod = SourceFileLoader("mios_mcp_server", _SERVER_PATH).load_module()
    _project_agent_card = getattr(_mcp_server_mod, "_project_agent_card", None)
except Exception:
    _project_agent_card = None


_GATE_EVIDENCE = {"diff_bytes": 7, "positive": True, "negative": True, "tree_restored": True, "exit_code": 0}

# The AgentCard closure of the official A2A v1 JSON Schema (a2a-protocol.org
# spec/a2a.json, "version": "v1"), pinned so the suite stays hermetic. Every
# validation keyword is verbatim; only description/title annotations are removed.
# MIOS_A2A_SCHEMA_PATH validates against a full downloaded copy instead.
_A2A_AGENT_CARD_SCHEMA = (
    '{"$defs":{"APIKeySecurityScheme":{"additionalProperties":false,"properties":{"description":{'
    '"default":"","type":"string"},"location":{"default":"","type":"string"},"name":{"default":""'
    ',"type":"string"}},"type":"object"},"AgentCapabilities":{"additionalProperties":false,"patte'
    'rnProperties":{"^(extended_agent_card)$":{"type":"boolean"},"^(push_notifications)$":{"type"'
    ':"boolean"}},"properties":{"extendedAgentCard":{"type":"boolean"},"extensions":{"items":{"$r'
    'ef":"#/$defs/AgentExtension"},"type":"array"},"pushNotifications":{"type":"boolean"},"stream'
    'ing":{"type":"boolean"}},"type":"object"},"AgentCard":{"additionalProperties":false,"pattern'
    'Properties":{"^(default_input_modes)$":{"items":{"type":"string"},"type":"array"},"^(default'
    '_output_modes)$":{"items":{"type":"string"},"type":"array"},"^(documentation_url)$":{"type":'
    '"string"},"^(icon_url)$":{"type":"string"},"^(security_requirements)$":{"items":{"$ref":"#/$'
    'defs/SecurityRequirement"},"type":"array"},"^(security_schemes)$":{"additionalProperties":{"'
    '$ref":"#/$defs/SecurityScheme"},"propertyNames":{"type":"string"},"type":"object"},"^(suppor'
    'ted_interfaces)$":{"items":{"$ref":"#/$defs/AgentInterface"},"type":"array"}},"properties":{'
    '"capabilities":{"$ref":"#/$defs/AgentCapabilities"},"defaultInputModes":{"items":{"type":"st'
    'ring"},"type":"array"},"defaultOutputModes":{"items":{"type":"string"},"type":"array"},"desc'
    'ription":{"default":"","type":"string"},"documentationUrl":{"type":"string"},"iconUrl":{"typ'
    'e":"string"},"name":{"default":"","type":"string"},"provider":{"$ref":"#/$defs/AgentProvider'
    '"},"securityRequirements":{"items":{"$ref":"#/$defs/SecurityRequirement"},"type":"array"},"s'
    'ecuritySchemes":{"additionalProperties":{"$ref":"#/$defs/SecurityScheme"},"propertyNames":{"'
    'type":"string"},"type":"object"},"signatures":{"items":{"$ref":"#/$defs/AgentCardSignature"}'
    ',"type":"array"},"skills":{"items":{"$ref":"#/$defs/AgentSkill"},"type":"array"},"supportedI'
    'nterfaces":{"items":{"$ref":"#/$defs/AgentInterface"},"type":"array"},"version":{"default":"'
    '","type":"string"}},"type":"object"},"AgentCardSignature":{"additionalProperties":false,"pro'
    'perties":{"header":{"$ref":"#/$defs/Struct"},"protected":{"default":"","type":"string"},"sig'
    'nature":{"default":"","type":"string"}},"type":"object"},"AgentExtension":{"additionalProper'
    'ties":false,"properties":{"description":{"default":"","type":"string"},"params":{"$ref":"#/$'
    'defs/Struct"},"required":{"default":false,"type":"boolean"},"uri":{"default":"","type":"stri'
    'ng"}},"type":"object"},"AgentInterface":{"additionalProperties":false,"patternProperties":{"'
    '^(protocol_binding)$":{"default":"","type":"string"},"^(protocol_version)$":{"default":"","t'
    'ype":"string"}},"properties":{"protocolBinding":{"default":"","type":"string"},"protocolVers'
    'ion":{"default":"","type":"string"},"tenant":{"default":"","type":"string"},"url":{"default"'
    ':"","type":"string"}},"type":"object"},"AgentProvider":{"additionalProperties":false,"proper'
    'ties":{"organization":{"default":"","type":"string"},"url":{"default":"","type":"string"}},"'
    'type":"object"},"AgentSkill":{"additionalProperties":false,"patternProperties":{"^(input_mod'
    'es)$":{"items":{"type":"string"},"type":"array"},"^(output_modes)$":{"items":{"type":"string'
    '"},"type":"array"},"^(security_requirements)$":{"items":{"$ref":"#/$defs/SecurityRequirement'
    '"},"type":"array"}},"properties":{"description":{"default":"","type":"string"},"examples":{"'
    'items":{"type":"string"},"type":"array"},"id":{"default":"","type":"string"},"inputModes":{"'
    'items":{"type":"string"},"type":"array"},"name":{"default":"","type":"string"},"outputModes"'
    ':{"items":{"type":"string"},"type":"array"},"securityRequirements":{"items":{"$ref":"#/$defs'
    '/SecurityRequirement"},"type":"array"},"tags":{"items":{"type":"string"},"type":"array"}},"t'
    'ype":"object"},"AuthorizationCodeOAuthFlow":{"additionalProperties":false,"patternProperties'
    '":{"^(authorization_url)$":{"default":"","type":"string"},"^(pkce_required)$":{"default":fal'
    'se,"type":"boolean"},"^(refresh_url)$":{"default":"","type":"string"},"^(token_url)$":{"defa'
    'ult":"","type":"string"}},"properties":{"authorizationUrl":{"default":"","type":"string"},"p'
    'kceRequired":{"default":false,"type":"boolean"},"refreshUrl":{"default":"","type":"string"},'
    '"scopes":{"additionalProperties":{"type":"string"},"propertyNames":{"type":"string"},"type":'
    '"object"},"tokenUrl":{"default":"","type":"string"}},"type":"object"},"ClientCredentialsOAut'
    'hFlow":{"additionalProperties":false,"patternProperties":{"^(refresh_url)$":{"default":"","t'
    'ype":"string"},"^(token_url)$":{"default":"","type":"string"}},"properties":{"refreshUrl":{"'
    'default":"","type":"string"},"scopes":{"additionalProperties":{"type":"string"},"propertyNam'
    'es":{"type":"string"},"type":"object"},"tokenUrl":{"default":"","type":"string"}},"type":"ob'
    'ject"},"DeviceCodeOAuthFlow":{"additionalProperties":false,"patternProperties":{"^(device_au'
    'thorization_url)$":{"default":"","type":"string"},"^(refresh_url)$":{"default":"","type":"st'
    'ring"},"^(token_url)$":{"default":"","type":"string"}},"properties":{"deviceAuthorizationUrl'
    '":{"default":"","type":"string"},"refreshUrl":{"default":"","type":"string"},"scopes":{"addi'
    'tionalProperties":{"type":"string"},"propertyNames":{"type":"string"},"type":"object"},"toke'
    'nUrl":{"default":"","type":"string"}},"type":"object"},"HTTPAuthSecurityScheme":{"additional'
    'Properties":false,"patternProperties":{"^(bearer_format)$":{"default":"","type":"string"}},"'
    'properties":{"bearerFormat":{"default":"","type":"string"},"description":{"default":"","type'
    '":"string"},"scheme":{"default":"","type":"string"}},"type":"object"},"ImplicitOAuthFlow":{"'
    'additionalProperties":false,"patternProperties":{"^(authorization_url)$":{"default":"","type'
    '":"string"},"^(refresh_url)$":{"default":"","type":"string"}},"properties":{"authorizationUr'
    'l":{"default":"","type":"string"},"refreshUrl":{"default":"","type":"string"},"scopes":{"add'
    'itionalProperties":{"type":"string"},"propertyNames":{"type":"string"},"type":"object"}},"ty'
    'pe":"object"},"MutualTlsSecurityScheme":{"additionalProperties":false,"properties":{"descrip'
    'tion":{"default":"","type":"string"}},"type":"object"},"OAuth2SecurityScheme":{"additionalPr'
    'operties":false,"patternProperties":{"^(oauth2_metadata_url)$":{"default":"","type":"string"'
    '}},"properties":{"description":{"default":"","type":"string"},"flows":{"$ref":"#/$defs/OAuth'
    'Flows"},"oauth2MetadataUrl":{"default":"","type":"string"}},"type":"object"},"OAuthFlows":{"'
    'additionalProperties":false,"patternProperties":{"^(authorization_code)$":{"$ref":"#/$defs/A'
    'uthorizationCodeOAuthFlow"},"^(client_credentials)$":{"$ref":"#/$defs/ClientCredentialsOAuth'
    'Flow"},"^(device_code)$":{"$ref":"#/$defs/DeviceCodeOAuthFlow"}},"properties":{"authorizatio'
    'nCode":{"$ref":"#/$defs/AuthorizationCodeOAuthFlow"},"clientCredentials":{"$ref":"#/$defs/Cl'
    'ientCredentialsOAuthFlow"},"deviceCode":{"$ref":"#/$defs/DeviceCodeOAuthFlow"},"implicit":{"'
    '$ref":"#/$defs/ImplicitOAuthFlow"},"password":{"$ref":"#/$defs/PasswordOAuthFlow"}},"type":"'
    'object"},"OpenIdConnectSecurityScheme":{"additionalProperties":false,"patternProperties":{"^'
    '(open_id_connect_url)$":{"default":"","type":"string"}},"properties":{"description":{"defaul'
    't":"","type":"string"},"openIdConnectUrl":{"default":"","type":"string"}},"type":"object"},"'
    'PasswordOAuthFlow":{"additionalProperties":false,"patternProperties":{"^(refresh_url)$":{"de'
    'fault":"","type":"string"},"^(token_url)$":{"default":"","type":"string"}},"properties":{"re'
    'freshUrl":{"default":"","type":"string"},"scopes":{"additionalProperties":{"type":"string"},'
    '"propertyNames":{"type":"string"},"type":"object"},"tokenUrl":{"default":"","type":"string"}'
    '},"type":"object"},"SecurityRequirement":{"additionalProperties":false,"properties":{"scheme'
    's":{"additionalProperties":{"$ref":"#/$defs/StringList"},"propertyNames":{"type":"string"},"'
    'type":"object"}},"type":"object"},"SecurityScheme":{"additionalProperties":false,"patternPro'
    'perties":{"^(api_key_security_scheme)$":{"$ref":"#/$defs/APIKeySecurityScheme"},"^(http_auth'
    '_security_scheme)$":{"$ref":"#/$defs/HTTPAuthSecurityScheme"},"^(mtls_security_scheme)$":{"$'
    'ref":"#/$defs/MutualTlsSecurityScheme"},"^(oauth2_security_scheme)$":{"$ref":"#/$defs/OAuth2'
    'SecurityScheme"},"^(open_id_connect_security_scheme)$":{"$ref":"#/$defs/OpenIdConnectSecurit'
    'yScheme"}},"properties":{"apiKeySecurityScheme":{"$ref":"#/$defs/APIKeySecurityScheme"},"htt'
    'pAuthSecurityScheme":{"$ref":"#/$defs/HTTPAuthSecurityScheme"},"mtlsSecurityScheme":{"$ref":'
    '"#/$defs/MutualTlsSecurityScheme"},"oauth2SecurityScheme":{"$ref":"#/$defs/OAuth2SecuritySch'
    'eme"},"openIdConnectSecurityScheme":{"$ref":"#/$defs/OpenIdConnectSecurityScheme"}},"type":"'
    'object"},"StringList":{"additionalProperties":false,"properties":{"list":{"items":{"type":"s'
    'tring"},"type":"array"}},"type":"object"},"Struct":{"type":"object"}},"$ref":"#/$defs/AgentC'
    'ard"}'
)


class TestA2ATranslateMapping(unittest.TestCase):
    """Validates ADR-0029 standards mapping, receipt invariants, and recursive credential refusal."""

    def test_adr0029_mapping_constants(self):
        """Verify presence and exact values of ADR-0029 state constants across standards."""
        # A2A v1.0.1 ProtoJSON Task States
        self.assertEqual(mios_translate.A2A_TASK_STATE_SUBMITTED, "TASK_STATE_SUBMITTED")
        self.assertEqual(mios_translate.A2A_TASK_STATE_WORKING, "TASK_STATE_WORKING")
        self.assertEqual(mios_translate.A2A_TASK_STATE_INPUT_REQUIRED, "TASK_STATE_INPUT_REQUIRED")
        self.assertEqual(mios_translate.A2A_TASK_STATE_COMPLETED, "TASK_STATE_COMPLETED")
        self.assertEqual(mios_translate.A2A_TASK_STATE_FAILED, "TASK_STATE_FAILED")
        self.assertEqual(mios_translate.A2A_TASK_STATE_REJECTED, "TASK_STATE_REJECTED")
        self.assertEqual(mios_translate.A2A_TASK_STATE_CANCELED, "TASK_STATE_CANCELED")

        self.assertEqual(
            mios_translate.A2A_TASK_STATES,
            {
                "TASK_STATE_SUBMITTED",
                "TASK_STATE_WORKING",
                "TASK_STATE_INPUT_REQUIRED",
                "TASK_STATE_COMPLETED",
                "TASK_STATE_FAILED",
                "TASK_STATE_REJECTED",
                "TASK_STATE_CANCELED",
            },
        )

        # MCP Tasks extension, at the revision mios.toml [mcp].protocol_version declares
        self.assertEqual(mios_translate.MCP_TASK_STATUS_WORKING, "working")
        self.assertEqual(mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED, "input_required")
        self.assertEqual(mios_translate.MCP_TASK_STATUS_COMPLETED, "completed")
        self.assertEqual(mios_translate.MCP_TASK_STATUS_FAILED, "failed")
        self.assertEqual(mios_translate.MCP_TASK_STATUS_CANCELLED, "cancelled")

        # OpenAI Responses Statuses
        self.assertEqual(mios_translate.OPENAI_STATUS_QUEUED, "queued")
        self.assertEqual(mios_translate.OPENAI_STATUS_IN_PROGRESS, "in_progress")
        self.assertEqual(mios_translate.OPENAI_STATUS_INCOMPLETE, "incomplete")
        self.assertEqual(mios_translate.OPENAI_STATUS_COMPLETED, "completed")
        self.assertEqual(mios_translate.OPENAI_STATUS_FAILED, "failed")
        self.assertEqual(mios_translate.OPENAI_STATUS_CANCELLED, "cancelled")

        # OpenTelemetry GenAI Semantic Conventions
        self.assertEqual(mios_translate.OTEL_GENAI_AGENT_ID, "gen_ai.agent.id")
        self.assertEqual(mios_translate.OTEL_GENAI_AGENT_NAME, "gen_ai.agent.name")
        self.assertEqual(mios_translate.OTEL_GENAI_CONVERSATION_ID, "gen_ai.conversation.id")
        self.assertEqual(mios_translate.OTEL_GENAI_OPERATION_NAME, "gen_ai.operation.name")

    def test_map_task_state_authoritative_table(self):
        """Verify map_task_state bidirectional transformations for all rows in ADR-0029."""
        cases = [
            # 1. receipt queued / submitted
            ("loop.v1", "queued", "a2a", mios_translate.A2A_TASK_STATE_SUBMITTED),
            ("loop.v1", "queued", "responses", mios_translate.OPENAI_STATUS_QUEUED),
            ("a2a", mios_translate.A2A_TASK_STATE_SUBMITTED, "loop.v1", "queued"),
            ("responses", mios_translate.OPENAI_STATUS_QUEUED, "a2a", mios_translate.A2A_TASK_STATE_SUBMITTED),
            ("responses", mios_translate.OPENAI_STATUS_QUEUED, "loop.v1", "queued"),

            # 2. agent.report working
            ("loop.v1", "working", "a2a", mios_translate.A2A_TASK_STATE_WORKING),
            ("loop.v1", "working", "mcp_tasks", mios_translate.MCP_TASK_STATUS_WORKING),
            ("loop.v1", "working", "responses", mios_translate.OPENAI_STATUS_IN_PROGRESS),
            ("a2a", mios_translate.A2A_TASK_STATE_WORKING, "loop.v1", "working"),
            ("a2a", mios_translate.A2A_TASK_STATE_WORKING, "mcp_tasks", mios_translate.MCP_TASK_STATUS_WORKING),
            ("a2a", mios_translate.A2A_TASK_STATE_WORKING, "responses", mios_translate.OPENAI_STATUS_IN_PROGRESS),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_WORKING, "loop.v1", "working"),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_WORKING, "a2a", mios_translate.A2A_TASK_STATE_WORKING),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_WORKING, "responses", mios_translate.OPENAI_STATUS_IN_PROGRESS),
            ("responses", mios_translate.OPENAI_STATUS_IN_PROGRESS, "loop.v1", "working"),
            ("responses", mios_translate.OPENAI_STATUS_IN_PROGRESS, "a2a", mios_translate.A2A_TASK_STATE_WORKING),
            ("responses", mios_translate.OPENAI_STATUS_IN_PROGRESS, "mcp_tasks", mios_translate.MCP_TASK_STATUS_WORKING),

            # 3. agent.report blocked (needs input)
            ("loop.v1", "blocked", "a2a", mios_translate.A2A_TASK_STATE_INPUT_REQUIRED),
            ("loop.v1", "blocked", "mcp_tasks", mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED),
            ("loop.v1", "blocked", "responses", mios_translate.OPENAI_STATUS_INCOMPLETE),
            ("a2a", mios_translate.A2A_TASK_STATE_INPUT_REQUIRED, "loop.v1", "blocked"),
            ("a2a", mios_translate.A2A_TASK_STATE_INPUT_REQUIRED, "mcp_tasks", mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED),
            ("a2a", mios_translate.A2A_TASK_STATE_INPUT_REQUIRED, "responses", mios_translate.OPENAI_STATUS_INCOMPLETE),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED, "loop.v1", "blocked"),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED, "a2a", mios_translate.A2A_TASK_STATE_INPUT_REQUIRED),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED, "responses", mios_translate.OPENAI_STATUS_INCOMPLETE),
            ("responses", mios_translate.OPENAI_STATUS_INCOMPLETE, "loop.v1", "blocked"),
            ("responses", mios_translate.OPENAI_STATUS_INCOMPLETE, "a2a", mios_translate.A2A_TASK_STATE_INPUT_REQUIRED),
            ("responses", mios_translate.OPENAI_STATUS_INCOMPLETE, "mcp_tasks", mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED),

            # 4. agent.report completed with gate evidence
            ("loop.v1", "delivered", "a2a", mios_translate.A2A_TASK_STATE_COMPLETED),
            ("loop.v1", "delivered", "mcp_tasks", mios_translate.MCP_TASK_STATUS_COMPLETED),
            ("loop.v1", "delivered", "responses", mios_translate.OPENAI_STATUS_COMPLETED),
            ("a2a", mios_translate.A2A_TASK_STATE_COMPLETED, "loop.v1", "delivered"),
            ("a2a", mios_translate.A2A_TASK_STATE_COMPLETED, "mcp_tasks", mios_translate.MCP_TASK_STATUS_COMPLETED),
            ("a2a", mios_translate.A2A_TASK_STATE_COMPLETED, "responses", mios_translate.OPENAI_STATUS_COMPLETED),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_COMPLETED, "loop.v1", "delivered"),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_COMPLETED, "a2a", mios_translate.A2A_TASK_STATE_COMPLETED),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_COMPLETED, "responses", mios_translate.OPENAI_STATUS_COMPLETED),
            ("responses", mios_translate.OPENAI_STATUS_COMPLETED, "loop.v1", "delivered"),
            ("responses", mios_translate.OPENAI_STATUS_COMPLETED, "a2a", mios_translate.A2A_TASK_STATE_COMPLETED),
            ("responses", mios_translate.OPENAI_STATUS_COMPLETED, "mcp_tasks", mios_translate.MCP_TASK_STATUS_COMPLETED),

            # 5. failure or refusal
            ("loop.v1", "errored", "a2a", mios_translate.A2A_TASK_STATE_FAILED),
            ("loop.v1", "errored", "mcp_tasks", mios_translate.MCP_TASK_STATUS_FAILED),
            ("loop.v1", "errored", "responses", mios_translate.OPENAI_STATUS_FAILED),
            ("a2a", mios_translate.A2A_TASK_STATE_FAILED, "loop.v1", "errored"),
            ("a2a", mios_translate.A2A_TASK_STATE_FAILED, "mcp_tasks", mios_translate.MCP_TASK_STATUS_FAILED),
            ("a2a", mios_translate.A2A_TASK_STATE_FAILED, "responses", mios_translate.OPENAI_STATUS_FAILED),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_FAILED, "loop.v1", "errored"),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_FAILED, "a2a", mios_translate.A2A_TASK_STATE_FAILED),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_FAILED, "responses", mios_translate.OPENAI_STATUS_FAILED),
            ("loop.v1", "refused", "a2a", mios_translate.A2A_TASK_STATE_REJECTED),
            ("loop.v1", "refused", "mcp_tasks", mios_translate.MCP_TASK_STATUS_FAILED),
            ("loop.v1", "refused", "responses", mios_translate.OPENAI_STATUS_FAILED),
            ("a2a", mios_translate.A2A_TASK_STATE_REJECTED, "loop.v1", "refused"),

            # 6. cancel
            ("loop.v1", "cancelled", "a2a", mios_translate.A2A_TASK_STATE_CANCELED),
            ("loop.v1", "cancelled", "mcp_tasks", mios_translate.MCP_TASK_STATUS_CANCELLED),
            ("loop.v1", "cancelled", "responses", mios_translate.OPENAI_STATUS_CANCELLED),
            ("a2a", mios_translate.A2A_TASK_STATE_CANCELED, "loop.v1", "cancelled"),
            ("a2a", mios_translate.A2A_TASK_STATE_CANCELED, "mcp_tasks", mios_translate.MCP_TASK_STATUS_CANCELLED),
            ("a2a", mios_translate.A2A_TASK_STATE_CANCELED, "responses", mios_translate.OPENAI_STATUS_CANCELLED),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_CANCELLED, "loop.v1", "cancelled"),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_CANCELLED, "a2a", mios_translate.A2A_TASK_STATE_CANCELED),
            ("mcp_tasks", mios_translate.MCP_TASK_STATUS_CANCELLED, "responses", mios_translate.OPENAI_STATUS_CANCELLED),
            ("responses", mios_translate.OPENAI_STATUS_CANCELLED, "loop.v1", "cancelled"),
            ("responses", mios_translate.OPENAI_STATUS_CANCELLED, "a2a", mios_translate.A2A_TASK_STATE_CANCELED),
            ("responses", mios_translate.OPENAI_STATUS_CANCELLED, "mcp_tasks", mios_translate.MCP_TASK_STATUS_CANCELLED),
        ]

        for src, st, tgt, expected in cases:
            res = mios_translate.map_task_state(src, st, tgt)
            self.assertEqual(res, expected, f"Mapping failed for {src}:{st} -> {tgt} (got {res}, expected {expected})")

    def test_receipt_invariant_two_sided(self):
        """Enforces ADR-0029 invariant: receipt 'received' maps to SUBMITTED / queued, NEVER WORKING or COMPLETED."""
        # Positive control 1: 'received' maps to A2A TASK_STATE_SUBMITTED
        res_a2a = mios_translate.map_task_state("receipt", "received", "a2a")
        self.assertEqual(res_a2a, mios_translate.A2A_TASK_STATE_SUBMITTED)

        # Positive control 2: 'received' maps to Responses 'queued'
        res_resp = mios_translate.map_task_state("receipt", "received", "responses")
        self.assertEqual(res_resp, mios_translate.OPENAI_STATUS_QUEUED)

        # Positive control 3: 'received' maps to loop.v1 'queued'
        res_loop = mios_translate.map_task_state("receipt", "received", "loop.v1")
        self.assertEqual(res_loop, "queued")

        # Positive control 4: Direct or embedded receipt in A2A frame translates to loop.v1 terminal 'queued'
        translated = mios_translate.translate({
            "source": "a2a",
            "frames": [{"receipt": {"status": "received"}}],
        })
        self.assertEqual(len(translated["events"]), 1)
        self.assertEqual(translated["events"][0]["kind"], "terminal")
        self.assertEqual(translated["events"][0]["status"], "queued")
        self.assertNotEqual(translated["events"][0]["status"], "working")
        self.assertNotEqual(translated["events"][0]["status"], "delivered")

        # Planted negative controls: enforce_receipt_invariant blocks illegal transitions
        with self.assertRaises(ValueError) as cm1:
            mios_translate.enforce_receipt_invariant("received", "WORKING")
        self.assertIn("RECEIPT INVARIANT VIOLATION", str(cm1.exception))

        with self.assertRaises(ValueError) as cm2:
            mios_translate.enforce_receipt_invariant("received", "TASK_STATE_WORKING")
        self.assertIn("RECEIPT INVARIANT VIOLATION", str(cm2.exception))

        with self.assertRaises(ValueError) as cm3:
            mios_translate.enforce_receipt_invariant("received", "COMPLETED")
        self.assertIn("RECEIPT INVARIANT VIOLATION", str(cm3.exception))

        with self.assertRaises(ValueError) as cm4:
            mios_translate.enforce_receipt_invariant("received", "TASK_STATE_COMPLETED")
        self.assertIn("RECEIPT INVARIANT VIOLATION", str(cm4.exception))

        with self.assertRaises(ValueError) as cm5:
            mios_translate.enforce_receipt_invariant("received", "IN_PROGRESS")
        self.assertIn("RECEIPT INVARIANT VIOLATION", str(cm5.exception))

        # Planted negative control: mapping 'received' to mcp_tasks is refused (MCP has no submitted state)
        with self.assertRaises(ValueError) as cm6:
            mios_translate.map_task_state("receipt", "received", "mcp_tasks")
        self.assertIn("RECEIPT INVARIANT VIOLATION", str(cm6.exception))

    def test_recursive_credential_refusal_negative_controls(self):
        """Planted negative controls: sensitive credential keys are refused recursively across all frame structures."""
        keys = ["api_key", "access_token", "refresh_token", "authorization", "client_secret", "password", "private_key"]

        for key in keys:
            # 1. Top-level key refusal
            with self.assertRaises(ValueError) as cm_top:
                mios_translate.translate({
                    "source": "auto",
                    "frames": [{"role": "ROLE_USER", "parts": [{"text": "hi"}], key: "secret_token_123"}],
                })
            self.assertIn("CREDENTIAL FIELD REFUSED", str(cm_top.exception))

            # 2. Deeply nested key refusal
            with self.assertRaises(ValueError) as cm_nest:
                mios_translate.translate({
                    "source": "auto",
                    "frames": [{
                        "role": "ROLE_USER",
                        "parts": [{
                            "data": {
                                "meta": {
                                    "credentials": {
                                        key: "secret_token_456"
                                    }
                                }
                            }
                        }]
                    }],
                })
            self.assertIn("CREDENTIAL FIELD REFUSED", str(cm_nest.exception))

            # 3. Inside list structure
            with self.assertRaises(ValueError) as cm_list:
                mios_translate.translate({
                    "source": "auto",
                    "frames": [{
                        "role": "ROLE_USER",
                        "parts": [{"text": "query"}],
                        "headers": [{"key": "auth", key: "secret_val"}],
                    }],
                })
            self.assertIn("CREDENTIAL FIELD REFUSED", str(cm_list.exception))

            # 4. Normalized hyphenated variant refusal
            hyphen_key = key.replace("_", "-")
            with self.assertRaises(ValueError) as cm_hyphen:
                mios_translate.translate({
                    "source": "auto",
                    "frames": [{"role": "ROLE_USER", "parts": [{"text": "query"}], hyphen_key: "secret"}],
                })
            self.assertIn("CREDENTIAL FIELD REFUSED", str(cm_hyphen.exception))

        # Positive control: frame with harmless fields processes cleanly without error
        clean_res = mios_translate.translate({
            "source": "auto",
            "frames": [{
                "role": "ROLE_USER",
                "messageId": "msg_safe_01",
                "parts": [{"text": "List the files in /tmp"}],
                "contextId": "ctx_safe",
                "meta": {"topic": "administration", "safe_id": 100},
            }],
        })
        self.assertGreaterEqual(len(clean_res["events"]), 1)
        self.assertEqual(clean_res["events"][0]["text"], "List the files in /tmp")

    def test_parts_discrimination_no_kind_tag(self):
        """A2A v1.0.1 discriminates parts strictly by member presence (text, data), NEVER kind tag."""
        # 1. loop -> A2A projection emits parts without kind
        events = [
            {"kind": "text", "role": "user", "text": "hello a2a", "message_id": "m1"},
            {"kind": "tool_call", "name": "get_status", "call_id": "c1", "arguments": {"x": 1}},
            {"kind": "tool_output", "call_id": "c1", "output": {"status": "ok"}},
        ]
        a2a_items = mios_translate.a2a_items(events)
        self.assertEqual(len(a2a_items), 3)

        # Message 1 (text)
        self.assertNotIn("kind", a2a_items[0]["parts"][0])
        self.assertIn("text", a2a_items[0]["parts"][0])
        self.assertEqual(a2a_items[0]["parts"][0]["text"], "hello a2a")

        # Message 2 (tool_call in data)
        self.assertNotIn("kind", a2a_items[1]["parts"][0])
        self.assertIn("data", a2a_items[1]["parts"][0])
        self.assertIn("tool_call", a2a_items[1]["parts"][0]["data"])

        # Message 3 (tool_output in data)
        self.assertNotIn("kind", a2a_items[2]["parts"][0])
        self.assertIn("data", a2a_items[2]["parts"][0])
        self.assertIn("tool_output", a2a_items[2]["parts"][0]["data"])

        # 2. Inbound A2A parsing parses parts by member presence without needing kind tag
        parsed_events = mios_translate.a2a_to_loop(a2a_items)
        self.assertEqual(len(parsed_events), 3)
        self.assertEqual(parsed_events[0]["kind"], "text")
        self.assertEqual(parsed_events[0]["text"], "hello a2a")
        self.assertEqual(parsed_events[1]["kind"], "tool_call")
        self.assertEqual(parsed_events[1]["name"], "get_status")
        self.assertEqual(parsed_events[2]["kind"], "tool_output")
        self.assertEqual(parsed_events[2]["output"], {"status": "ok"})

    def test_bidirectional_roundtrip_loop_and_a2a(self):
        """Bidirectional conversion: loop.v1 <-> A2A without data loss or fabricated states."""
        orig_events = [
            {"kind": "text", "role": "user", "text": "Start analysis", "message_id": "msg_001"},
            {"kind": "tool_call", "name": "read_metrics", "call_id": "call_99", "arguments": {"interval": 60}},
            {"kind": "tool_output", "call_id": "call_99", "output": {"cpu": 12.5, "mem": 45.0}},
            {"kind": "terminal", "status": "delivered", "evidence": _GATE_EVIDENCE, "text": "", "task_id": "fixture-task-42"},
        ]

        # Forward: loop -> A2A
        a2a_proj = mios_translate.loop_to_a2a(orig_events, context_id="ctx_roundtrip")
        self.assertEqual(len(a2a_proj), 4)

        # Backward: A2A -> loop
        restored_events = mios_translate.a2a_to_loop(a2a_proj)
        self.assertEqual(len(restored_events), 4)

        self.assertEqual(restored_events[0]["kind"], "text")
        self.assertEqual(restored_events[0]["text"], orig_events[0]["text"])
        self.assertEqual(restored_events[1]["kind"], "tool_call")
        self.assertEqual(restored_events[1]["name"], orig_events[1]["name"])
        self.assertEqual(restored_events[1]["arguments"], orig_events[1]["arguments"])
        self.assertEqual(restored_events[2]["kind"], "tool_output")
        self.assertEqual(restored_events[2]["output"], orig_events[2]["output"])
        self.assertEqual(restored_events[3]["kind"], "terminal")
        self.assertEqual(restored_events[3]["status"], "delivered")

    def test_bidirectional_roundtrip_loop_and_responses(self):
        """Bidirectional conversion: loop.v1 <-> OpenAI Responses."""
        orig_events = [
            {"kind": "text", "role": "assistant", "text": "Executing task"},
            {"kind": "tool_call", "name": "run_command", "call_id": "call_1", "arguments": {"cmd": "uptime"}},
            {"kind": "tool_output", "call_id": "call_1", "output": "load average 0.05"},
        ]

        resp_items = mios_translate.loop_to_responses(orig_events)
        self.assertEqual(len(resp_items), 3)

        restored = mios_translate.responses_to_loop(resp_items)
        self.assertEqual(len(restored), 3)
        self.assertEqual(restored[0]["kind"], "text")
        self.assertEqual(restored[0]["text"], "Executing task")
        self.assertEqual(restored[1]["kind"], "tool_call")
        self.assertEqual(restored[1]["name"], "run_command")
        self.assertEqual(restored[1]["arguments"], {"cmd": "uptime"})
        self.assertEqual(restored[2]["kind"], "tool_output")
        self.assertEqual(restored[2]["output"], "load average 0.05")

    def test_bidirectional_roundtrip_loop_and_mcp_tasks(self):
        """Bidirectional conversion: loop.v1 <-> MCP Tasks extension."""
        orig_events = [
            {"kind": "terminal", "status": "working", "task_id": "fixture-mcp-1"},
            {"kind": "terminal", "status": "blocked", "task_id": "fixture-mcp-2"},
            {"kind": "terminal", "status": "delivered", "evidence": _GATE_EVIDENCE, "task_id": "fixture-mcp-3"},
            {"kind": "terminal", "status": "errored", "task_id": "fixture-mcp-4"},
            {"kind": "terminal", "status": "cancelled", "task_id": "fixture-mcp-5"},
        ]

        mcp_items = mios_translate.loop_to_mcp_tasks(orig_events)
        self.assertEqual(len(mcp_items), 5)
        self.assertEqual(mcp_items[0]["status"], mios_translate.MCP_TASK_STATUS_WORKING)
        self.assertEqual(mcp_items[1]["status"], mios_translate.MCP_TASK_STATUS_INPUT_REQUIRED)
        self.assertEqual(mcp_items[2]["status"], mios_translate.MCP_TASK_STATUS_COMPLETED)
        self.assertEqual(mcp_items[3]["status"], mios_translate.MCP_TASK_STATUS_FAILED)
        self.assertEqual(mcp_items[4]["status"], mios_translate.MCP_TASK_STATUS_CANCELLED)

        restored = mios_translate.mcp_tasks_to_loop(mcp_items)
        self.assertEqual(len(restored), 5)
        self.assertEqual([r["status"] for r in restored], ["working", "blocked", "delivered", "errored", "cancelled"])

    def test_openai_agents_sdk_handoff(self):
        """OpenAI Agents SDK transfer_to_<agent> maps to handoff event and A2A peer delegation."""
        # Function call representing SDK handoff
        call_frame = {
            "type": "function_call",
            "call_id": "call_handoff_42",
            "name": "transfer_to_worker_lane3",
            "arguments": json.dumps({"reason": "cloud catalogue inspection"}),
        }

        events = mios_translate._openai(call_frame)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "tool_call")
        self.assertEqual(events[0]["name"], "transfer_to_worker_lane3")
        self.assertEqual(events[0]["handoff_agent"], "worker_lane3")

        # Projecting to A2A emits ROLE_AGENT message with agent metadata
        a2a_out = mios_translate.a2a_items([
            {"kind": "handoff", "target_agent": "worker_lane3", "arguments": {"reason": "cloud catalogue"}}
        ])
        self.assertEqual(len(a2a_out), 1)
        self.assertEqual(a2a_out[0]["metadata"]["agent"], "worker_lane3")
        self.assertEqual(a2a_out[0]["parts"][0]["data"]["tool_call"]["name"], "transfer_to_worker_lane3")

    def test_otel_genai_semantic_conventions(self):
        """OpenTelemetry GenAI attributes extraction and construction per ADR-0029."""
        attrs = mios_translate.build_otel_genai_attributes(
            agent_id="agent_101",
            agent_name="relay_coordinator",
            conversation_id="conv_session_7",
            operation_name="invoke_agent",
        )
        self.assertEqual(attrs[mios_translate.OTEL_GENAI_AGENT_ID], "agent_101")
        self.assertEqual(attrs[mios_translate.OTEL_GENAI_AGENT_NAME], "relay_coordinator")
        self.assertEqual(attrs[mios_translate.OTEL_GENAI_CONVERSATION_ID], "conv_session_7")
        self.assertEqual(attrs[mios_translate.OTEL_GENAI_OPERATION_NAME], "invoke_agent")

        # Frame extraction
        frame = {
            "metadata": {
                "agent_id": "agt_55",
                "agent": "explorer_survey",
            },
            "contextId": "ctx_lane2",
        }
        extracted = mios_translate.extract_otel_genai_attributes(frame)
        self.assertEqual(extracted.get(mios_translate.OTEL_GENAI_AGENT_ID), "agt_55")
        self.assertEqual(extracted.get(mios_translate.OTEL_GENAI_AGENT_NAME), "explorer_survey")
        self.assertEqual(extracted.get(mios_translate.OTEL_GENAI_CONVERSATION_ID), "ctx_lane2")

    def test_translate_source_detection_and_validation(self):
        """translate() detects source dialects accurately and validates request envelope."""
        # Auto-detect A2A
        res_a2a = mios_translate.translate({
            "source": "auto",
            "frames": [{"role": "ROLE_USER", "parts": [{"text": "ping"}], "messageId": "m1"}],
        })
        self.assertGreaterEqual(len(res_a2a["events"]), 1)
        self.assertEqual(res_a2a["events"][0]["text"], "ping")

        # Auto-detect MCP tasks
        res_mcp = mios_translate.translate({
            "source": "auto",
            "frames": [{"taskId": "t1", "status": "working"}],
        })
        self.assertEqual(len(res_mcp["events"]), 1)
        self.assertEqual(res_mcp["events"][0]["status"], "working")

        # Auto-detect OpenAI Responses
        res_resp = mios_translate.translate({
            "source": "auto",
            "frames": [{"object": "response", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}]}],
        })
        self.assertGreaterEqual(len(res_resp["events"]), 1)
        self.assertEqual(res_resp["events"][0]["text"], "done")

        # Reject unknown source dialect
        with self.assertRaises(ValueError) as cm_unknown:
            mios_translate.translate({"source": "unsupported_protocol", "frames": []})
        self.assertIn("UNKNOWN SOURCE", str(cm_unknown.exception))


class TestInteropReviewNegatives(unittest.TestCase):
    def test_camel_case_and_header_credentials_are_refused(self):
        for key in ("apiKey", "accessToken", "clientSecret", "privateKey", "refreshToken", "x-api-key", "API.KEY"):
            with self.subTest(key=key):
                self.assertEqual(mios_translate.credential_key({"parts": [{"data": {key: "fixture"}}]}), key)
                with self.assertRaises(ValueError):
                    mios_translate.translate({"source": "a2a", "frames": [{"parts": [{"data": {key: "fixture"}}]}]})

    def test_internal_states_use_protocol_enums_and_unknowns_fail(self):
        for state in ("unverified", "gate_failed", "vacuous", "timed_out", "control_invalid"):
            with self.subTest(state=state):
                self.assertIn(mios_translate.map_task_state("loop.v1", state, "a2a"), mios_translate.A2A_TASK_STATES)
                self.assertIn(mios_translate.map_task_state("loop.v1", state, "mcp_tasks"), mios_translate.MCP_TASK_STATUSES)
        for source, state in (("a2a", "TASK_STATE_UNVERIFIED"), ("mcp_tasks", "gate_failed"), ("loop.v1", "invented")):
            with self.assertRaises(ValueError):
                mios_translate.map_task_state(source, state, "a2a")

    def test_completed_without_gate_artifact_is_not_delivered(self):
        task = {"id": "fixture-completion", "status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": []}
        self.assertEqual(mios_translate.a2a_to_loop([task])[0]["status"], "unverified")
        result = mios_translate.translate({"source": "a2a", "frames": [task]})
        self.assertNotEqual(result["events"][0]["status"], "delivered")
        self.assertNotEqual(result["a2a_items"][0]["status"]["state"], "TASK_STATE_COMPLETED")

    def test_completion_artifact_contains_gate_evidence(self):
        event = {"kind": "terminal", "status": "delivered", "task_id": "fixture-completion"}
        result = mios_translate.translate({"source": "loop.v1", "frames": [event], "evidence": _GATE_EVIDENCE})
        task = result["a2a_items"][0]
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertTrue(task["artifacts"][0]["artifactId"])
        self.assertEqual(task["artifacts"][0]["parts"][0]["data"]["gate_evidence"], _GATE_EVIDENCE)
        self.assertEqual(mios_translate.a2a_to_loop([task])[0]["status"], "delivered")
        for field in ("positive", "negative", "tree_restored", "exit_code", "diff_bytes"):
            broken = dict(_GATE_EVIDENCE)
            broken.pop(field)
            with self.subTest(field=field):
                item = mios_translate.a2a_items([{**event, "evidence": broken}])[0]
                self.assertNotEqual(item["status"]["state"], "TASK_STATE_COMPLETED")

    def test_no_fabricated_backlog_ids_and_context_is_exposed(self):
        event = {"kind": "terminal", "status": "working"}
        self.assertNotEqual(mios_translate.mcp_tasks_items([event])[0]["taskId"], "task_default")
        self.assertFalse(mios_translate.a2a_items([event])[0]["id"].startswith("task_"))
        schema = _mcp_server_mod._translation_tools()[0]["inputSchema"]
        self.assertEqual(schema["properties"]["context_id"], {"type": "string"})
        result = mios_translate.translate({"source": "loop.v1", "frames": [event], "context_id": "fixture-context"})
        self.assertEqual(result["a2a_items"][0]["contextId"], "fixture-context")


class TestA2AProjectedCard(unittest.TestCase):
    """Independent trust, provisioned keys, relay identity and official card schema."""

    def setUp(self):
        from unittest.mock import patch
        self.key = Ed25519PrivateKey.generate()
        self.data = _mcp_server_mod.mios_toml.load_merged()
        self.data["ports"]["agent_pipe"] = 32123
        self.data["meta"]["mios_version"] = "fixture-version"
        self.registration = {"agent_id": "proof-head", "kind": "generic", "label": "Proof head"}
        for patcher in [patch.object(_mcp_server_mod.mios_toml, "load_merged", return_value=self.data),
                        patch.object(_mcp_server_mod, "_agent_relay_call", return_value={"agents": [self.registration]}),
                        patch.object(_mcp_server_mod, "_load_card_signing_key", return_value=(self.key, "fixture-kid", self.key.public_key()))]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_projected_card_official_schema_and_trusted_signature(self):
        import jsonschema
        schema_path = os.environ.get("MIOS_A2A_SCHEMA_PATH")
        if schema_path:
            with open(schema_path, encoding="utf-8") as stream:
                schema = json.load(stream)
            schema["$ref"] = "#/$defs/AgentCard"
        else:
            schema = json.loads(_A2A_AGENT_CARD_SCHEMA)
        card = _project_agent_card("proof-head")
        jsonschema.Draft202012Validator(schema).validate(card)
        self.assertEqual(card["name"], "Proof head")
        self.assertEqual(card["version"], "fixture-version")
        self.assertIn(":32123/a2a", card["supportedInterfaces"][0]["url"])
        self.assertNotIn("provider", card)
        self.assertNotIn("x-mios", card)
        self.assertNotIn("tools", card["capabilities"])
        self.assertIn("securityRequirements", card)
        self.assertEqual(_verify_agent_card_signature(card, public_key=self.key.public_key()), (True, "ok"))
        # Plant the old fields; strict stock schema must catch a real schema defect.
        bad = json.loads(json.dumps(card))
        for key, value in (("version", {"protocol": "1.0"}), ("security", [{"bearer": []}]),
                           ("x-mios", {"kind": "fixture"}), ("capabilities", {"tools": True})):
            bad = {**card, key: value}
            with self.subTest(key=key), self.assertRaises(jsonschema.ValidationError):
                jsonschema.Draft202012Validator(schema).validate(bad)

    def test_attacker_replaces_embedded_key_and_resigns(self):
        from mios_pipe.federation.agentcard_sign import _agent_card_signature
        attacker = Ed25519PrivateKey.generate()
        card = _project_agent_card("proof-head")
        card["supportedInterfaces"][0]["url"] = "http://evil.example/a2a"
        card["x-mios"] = {"publicKey": _b64u(attacker.public_key().public_bytes_raw())}
        card["signatures"] = [_agent_card_signature(card, load_priv_fn=lambda: attacker, kid_fn=lambda: "attacker")]
        self.assertEqual(_verify_agent_card_signature(card, public_key=self.key.public_key()), (False, "invalid_signature"))
        self.assertEqual(_verify_agent_card_signature(card, load_pub_fn=lambda _: None), (False, "no_public_key"))

    def test_tampered_card_rejected(self):
        card = _project_agent_card("proof-head")
        card["name"] = "imposter"
        self.assertEqual(_verify_agent_card_signature(card, public_key=self.key.public_key()), (False, "invalid_signature"))

    def test_cli_missing_key_fails_without_output(self):
        from unittest.mock import patch
        import io
        with patch.object(_mcp_server_mod, "_load_card_signing_key", side_effect=RuntimeError("missing passport")), patch("sys.stdout", new_callable=io.StringIO) as output:
            with self.assertRaisesRegex(RuntimeError, "missing passport"):
                _mcp_server_mod._agent_cli_main(["--card", "proof-head"])
            self.assertEqual(output.getvalue(), "")

    def test_card_projects_without_agent_pipe_on_the_path(self):
        # `mios agents --card` runs the server with only usr/lib/mios on sys.path.
        probe = (
            "import sys\n"
            "from importlib.machinery import SourceFileLoader\n"
            "from unittest.mock import patch\n"
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey\n"
            "assert not any('agent-pipe' in entry for entry in sys.path)\n"
            "server = SourceFileLoader('mios_mcp_server', sys.argv[1]).load_module()\n"
            "key = Ed25519PrivateKey.generate()\n"
            "listing = {'agents': [{'agent_id': 'proof-head', 'kind': 'generic'}]}\n"
            "with patch.object(server, '_agent_relay_call', return_value=listing), \\\n"
            "        patch.object(server, '_load_card_signing_key',\n"
            "                     return_value=(key, 'fixture-kid', key.public_key())):\n"
            "    print(len(server._project_agent_card('proof-head')['signatures']))\n"
        )
        result = subprocess.run([sys.executable, "-I", "-c", probe, _SERVER_PATH],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1")

    def test_card_must_verify_under_the_provisioned_key(self):
        from unittest.mock import patch
        other = Ed25519PrivateKey.generate().public_key()
        with patch.object(_mcp_server_mod, "_load_card_signing_key",
                          return_value=(self.key, "fixture-kid", other)):
            with self.assertRaisesRegex(RuntimeError, "provisioned passport public key"):
                _project_agent_card("proof-head")
        # A verifier that resolves the trusted key by registered identity accepts it.
        card = _project_agent_card("proof-head")
        trusted = {"proof-head": self.key.public_key()}.get
        self.assertEqual(_verify_agent_card_signature(card, load_pub_fn=trusted, passport_agent_name="proof-head"),
                         (True, "ok"))

    def test_unknown_registration_rejected_even_if_cli_catalogued(self):
        with self.assertRaisesRegex(ValueError, "Unknown agent"):
            _project_agent_card("catalogue-only", {"tools": [{"name": "catalogue-only"}]})

    def test_missing_passport_has_no_derivable_fallback(self):
        from unittest.mock import patch
        # Load the real function independently of the positive fixture's mock.
        import ast
        source = Path(_SERVER_PATH).read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "_load_card_signing_key")
        namespace = dict(vars(_mcp_server_mod))
        exec(compile(ast.Module(body=[node], type_ignores=[]), _SERVER_PATH, "exec"), namespace)
        loader = namespace["_load_card_signing_key"]
        with tempfile.TemporaryDirectory() as directory:
            self.data["passport"] = {"enable": True, "key_dir": directory}
            with self.assertRaisesRegex(RuntimeError, "0600 passport"):
                loader("proof-head")
            key_path = Path(directory) / "proof-head"
            key_path.mkdir()
            (key_path / "private.key").write_bytes(b"invalid key")
            (key_path / "private.key").chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "0600 passport"):
                loader("proof-head")
            with self.assertRaises(ValueError):
                loader("../escaped")
            if os.name != "nt":
                from cryptography.hazmat.primitives import serialization
                (key_path / "private.key").write_bytes(self.key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
                (key_path / "private.key").chmod(0o600)
                (key_path / "kid").write_text("fixture-kid")
                with self.assertRaisesRegex(RuntimeError, "provisioned passport public key"):
                    loader("proof-head")
                pem = serialization.PublicFormat.SubjectPublicKeyInfo
                (key_path / "public.key").write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(
                    serialization.Encoding.PEM, pem))
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    loader("proof-head")
                (key_path / "public.key").write_bytes(self.key.public_key().public_bytes(
                    serialization.Encoding.PEM, pem))
                private, kid, public = loader("proof-head")
                self.assertEqual(kid, "fixture-kid")
                self.assertEqual(public.public_bytes_raw(), self.key.public_key().public_bytes_raw())
                self.assertIsNotNone(private)



if __name__ == "__main__":
    unittest.main()
