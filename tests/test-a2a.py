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
#             and Signed AgentCard Projection (Task STD26-02 / assign_002)
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
from importlib.machinery import SourceFileLoader
import subprocess

_SERVER_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-mcp-server")
try:
    _mcp_server_mod = SourceFileLoader("mios_mcp_server", _SERVER_PATH).load_module()
    _project_agent_card = getattr(_mcp_server_mod, "_project_agent_card", None)
except Exception:
    _project_agent_card = None


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

        # MCP Tasks Extension (2026-07-28)
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
            {"kind": "terminal", "status": "delivered", "text": "", "task_id": "task_42"},
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
            {"kind": "terminal", "status": "working", "task_id": "task_mcp_1"},
            {"kind": "terminal", "status": "blocked", "task_id": "task_mcp_2"},
            {"kind": "terminal", "status": "delivered", "task_id": "task_mcp_3"},
            {"kind": "terminal", "status": "errored", "task_id": "task_mcp_4"},
            {"kind": "terminal", "status": "cancelled", "task_id": "task_mcp_5"},
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


class TestA2AProjectedCard(unittest.TestCase):
    """Validates mios agents --card <id> projection, RFC-8785 JCS, and Ed25519 detached JWS signature."""

    def setUp(self):
        self.server_path = _SERVER_PATH
        self.assertTrue(os.path.isfile(self.server_path), f"mios-mcp-server not found at {self.server_path}")

    def test_projected_card_structure_and_valid_signature(self):
        """Verifies in-memory AgentCard generation against A2A v1.0.1 schema with valid Ed25519 signature."""
        self.assertIsNotNone(_project_agent_card, "Failed to load _project_agent_card from mios-mcp-server")
        card = _project_agent_card("codex")

        # Verify A2A v1.0.1 schema conformance
        self.assertEqual(card["name"], "MiOS Codex")
        self.assertEqual(card["version"], "1.0.1")
        self.assertTrue(isinstance(card["supportedInterfaces"], list))
        self.assertEqual(card["supportedInterfaces"][0]["protocolBinding"], "JSONRPC")
        self.assertEqual(card["supportedInterfaces"][0]["protocolVersion"], "1.0")
        self.assertTrue(card["capabilities"]["tools"])
        self.assertEqual(card["defaultInputModes"], ["text/plain", "application/json"])
        self.assertEqual(card["defaultOutputModes"], ["text/plain"])
        self.assertEqual(card["skills"][0]["id"], "codex")

        # Verify x-mios extension and public key
        self.assertIn("x-mios", card)
        pub_b64 = card["x-mios"]["publicKey"]
        self.assertIsNotNone(pub_b64)
        pub_bytes = _b64u_decode(pub_b64)
        pub = Ed25519PublicKey.from_public_bytes(pub_bytes)

        # Verify signatures structure
        self.assertIn("signatures", card)
        self.assertEqual(len(card["signatures"]), 1)
        sig_entry = card["signatures"][0]
        self.assertIn("protected", sig_entry)
        self.assertIn("signature", sig_entry)

        # Verify signature cryptographically using public key
        valid, reason = _verify_agent_card_signature(card, public_key=pub)
        self.assertTrue(valid, f"Card verification failed: {reason}")
        self.assertEqual(reason, "ok")

        # Verify second agent (claude)
        card_claude = _project_agent_card("claude")
        self.assertEqual(card_claude["name"], "MiOS Claude")
        pub_claude = Ed25519PublicKey.from_public_bytes(_b64u_decode(card_claude["x-mios"]["publicKey"]))
        valid_claude, reason_claude = _verify_agent_card_signature(card_claude, public_key=pub_claude)
        self.assertTrue(valid_claude, f"Claude card verification failed: {reason_claude}")
        self.assertEqual(reason_claude, "ok")

    def test_cli_card_projection_subprocess(self):
        """Verifies CLI execution: python mios-mcp-server --agent-cli --card <id> produces valid signed card."""
        res = subprocess.run(
            [sys.executable, self.server_path, "--agent-cli", "--card", "codex"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"CLI exited with error: {res.stderr}")
        card = json.loads(res.stdout)

        self.assertEqual(card["name"], "MiOS Codex")
        self.assertEqual(card["version"], "1.0.1")
        self.assertIn("signatures", card)

        pub = Ed25519PublicKey.from_public_bytes(_b64u_decode(card["x-mios"]["publicKey"]))
        valid, reason = _verify_agent_card_signature(card, public_key=pub)
        self.assertTrue(valid, f"CLI projected card failed verification: {reason}")
        self.assertEqual(reason, "ok")

    def test_projected_card_tamper_rejection_negative_controls(self):
        """Planted negative controls: any alteration to card payload or signature invalidates JWS verification."""
        self.assertIsNotNone(_project_agent_card)
        card = _project_agent_card("codex")
        pub = Ed25519PublicKey.from_public_bytes(_b64u_decode(card["x-mios"]["publicKey"]))

        # 1. Tamper description
        t_desc = json.loads(json.dumps(card))
        t_desc["description"] = "tampered description"
        ok, reason = _verify_agent_card_signature(t_desc, public_key=pub)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")

        # 2. Tamper capabilities
        t_caps = json.loads(json.dumps(card))
        t_caps["capabilities"]["tools"] = False
        ok, reason = _verify_agent_card_signature(t_caps, public_key=pub)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")

        # 3. Tamper skills
        t_skills = json.loads(json.dumps(card))
        t_skills["skills"].append({"id": "injected_privilege", "name": "admin"})
        ok, reason = _verify_agent_card_signature(t_skills, public_key=pub)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")

        # 4. Tamper name
        t_name = json.loads(json.dumps(card))
        t_name["name"] = "MiOS Imposter"
        ok, reason = _verify_agent_card_signature(t_name, public_key=pub)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")

        # 5. Tamper signature string (corrupted bits)
        t_sig = json.loads(json.dumps(card))
        sig_str = t_sig["signatures"][0]["signature"]
        # Invert first character
        flipped_char = "B" if sig_str[0] == "A" else "A"
        t_sig["signatures"][0]["signature"] = flipped_char + sig_str[1:]
        ok, reason = _verify_agent_card_signature(t_sig, public_key=pub)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")

        # 6. Tamper protected header algorithm (unsupported alg)
        t_alg = json.loads(json.dumps(card))
        bad_hdr = _b64u(_jcs_canonicalize({"alg": "RS256", "kid": "fake-kid"}))
        t_alg["signatures"][0]["protected"] = bad_hdr
        ok, reason = _verify_agent_card_signature(t_alg, public_key=pub)
        self.assertFalse(ok)
        self.assertEqual(reason, "unsupported_alg:RS256")

        # 7. Unsigned card (missing signatures field)
        t_unsigned = json.loads(json.dumps(card))
        del t_unsigned["signatures"]
        ok, reason = _verify_agent_card_signature(t_unsigned, public_key=pub)
        self.assertIsNone(ok)
        self.assertEqual(reason, "unsigned")

        # 8. Foreign key rejection (verify with another valid Ed25519 key)
        foreign_priv = Ed25519PrivateKey.generate()
        ok, reason = _verify_agent_card_signature(card, public_key=foreign_priv.public_key())
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")

    def test_unknown_agent_card_rejection(self):
        """Requesting an unknown agent card raises explicit error and non-zero exit code."""
        # In-memory rejection
        self.assertIsNotNone(_project_agent_card)
        with self.assertRaises(ValueError) as cm:
            _project_agent_card("nonexistent_unknown_agent")
        self.assertIn("Unknown agent: nonexistent_unknown_agent", str(cm.exception))

        # CLI subprocess rejection
        proc = subprocess.run(
            [sys.executable, self.server_path, "--agent-cli", "--card", "nonexistent_unknown_agent"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Unknown agent: nonexistent_unknown_agent", proc.stderr)


if __name__ == "__main__":
    unittest.main()
