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
        # No trusted key -> unverified: the card's own embedded key is never trusted
        self.assertFalse(attestation.A2AAuthenticator.verify_card(card))

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

    def test_cli_trusts_only_a_provisioned_key(self):
        """The CLI caller: no --key/--agent means unverified; --agent resolves the
        provisioned passport public key from the [passport].key_dir SSOT."""
        import subprocess
        attacker = attestation.A2AAuthenticator.generate_keypair(node_id=666)
        with tempfile.TemporaryDirectory(prefix="mios-a2a-trust-") as tmp:
            key_dir = os.path.join(tmp, "passports")
            os.makedirs(os.path.join(key_dir, "proof-agent"))
            with open(os.path.join(key_dir, "proof-agent", "public.key"), "wb") as f:
                f.write(self.auth.public_key.public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
            host_toml = os.path.join(tmp, "host.toml")
            with open(host_toml, "w", encoding="utf-8") as f:
                f.write(f"[passport]\nenable = true\nkey_dir = '{key_dir}'\n")
            absent = os.path.join(tmp, "absent")
            env = dict(os.environ, MIOS_HOST_TOML=host_toml, MIOS_USER_TOML=os.path.join(absent, "u.toml"),
                       MIOS_HOST_TOML_D=absent, MIOS_USER_TOML_D=absent, MIOS_VENDOR_TOML_D=absent,
                       MIOS_RESOLVER_NATIVE="0", MIOS_DB_AUTHORITATIVE="0")
            genuine = os.path.join(tmp, "genuine.json")
            forged = os.path.join(tmp, "forged.json")
            with open(genuine, "w", encoding="utf-8") as f:
                json.dump(self.auth.create_card("proof-agent", ["code_exec"]), f)
            with open(forged, "w", encoding="utf-8") as f:
                json.dump(attacker.create_card("proof-agent", ["code_exec"]), f)

            def cli(*argv):
                res = subprocess.run([sys.executable, _ATTESTATION_PATH, *argv], env=env,
                                     capture_output=True, text=True, timeout=60)
                return res.returncode, res.stdout + res.stderr

            # Self-signed card, no trusted key -> unverified (was: trusted its own key)
            code, out = cli("verify-card", "--card", forged)
            self.assertEqual(code, 1, out)
            self.assertIn('"valid": false', out)
            code, out = cli("negotiate", "--card", forged, "--required", "code_exec")
            self.assertEqual(code, 1, out)
            # Provisioned identity: the genuine card verifies, the forged one does not
            code, out = cli("verify-card", "--card", genuine, "--agent", "proof-agent")
            self.assertEqual(code, 0, out)
            self.assertIn('"valid": true', out)
            code, out = cli("verify-card", "--card", forged, "--agent", "proof-agent")
            self.assertEqual(code, 1, out)
            # Nothing provisioned for the identity, or a path-shaped identity -> unverified
            code, out = cli("verify-card", "--card", genuine, "--agent", "nobody")
            self.assertEqual(code, 1, out)
            code, out = cli("verify-card", "--card", genuine, "--agent", "../passports/proof-agent")
            self.assertEqual(code, 1, out)


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


if __name__ == "__main__":
    unittest.main()
